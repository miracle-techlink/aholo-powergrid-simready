"""CPU PLY import with explicit 3DGS encoding and portable USD appearance.

The 3DGS layout is the reference implementation's log scales, opacity logits,
wxyz quaternions and channel-major SH rest coefficients. RGB point clouds are
also supported as USD points, without pretending they are Gaussian surfaces.
"""

from pathlib import Path

import numpy as np

PLY_TYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "<i2",
    "int16": "<i2",
    "ushort": "<u2",
    "uint16": "<u2",
    "int": "<i4",
    "int32": "<i4",
    "uint": "<u4",
    "uint32": "<u4",
    "float": "<f4",
    "float32": "<f4",
    "double": "<f8",
    "float64": "<f8",
}
MAX_POINTS = 2_000_000
MAX_VERTEX_BYTES = 768 * 1024 * 1024


def finite_array(value, name, shape=None):
    result = np.asarray(value, dtype=np.float64)
    if (shape is not None and result.shape != shape) or not np.isfinite(result).all():
        raise ValueError(f"Invalid finite {name}, expected shape {shape}")
    return result


def validate_sim3(value):
    """Return a positive, orientation-preserving column-vector similarity."""
    transform = finite_array(value, "source_to_world", (4, 4))
    if not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-9, rtol=0):
        raise ValueError("source_to_world must be affine")
    linear = transform[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0]))
    if (
        not 1e-8 <= scale <= 1e8
        or not np.allclose(
            linear.T @ linear, np.eye(3) * scale**2, rtol=1e-6, atol=1e-12 * scale**2
        )
        or np.linalg.det(linear) <= 0
    ):
        raise ValueError("source_to_world must be positive uniform scale and rotation")
    return transform


def read_ply(path, max_points=MAX_POINTS):
    """Read the vertex table only; refuse huge allocations and ambiguous formats."""
    path = Path(path)
    if type(max_points) is not int or not 1 <= max_points <= MAX_POINTS:
        raise ValueError(f"max_points must be within 1..{MAX_POINTS}")
    with path.open("rb") as stream:
        if stream.readline().strip() != b"ply":
            raise ValueError("Not a PLY file")
        count, properties, elements = None, [], []
        in_vertex, binary = False, False
        for _ in range(4096):
            line = stream.readline(4097)
            if not line or len(line) > 4096:
                raise ValueError("Truncated or oversized PLY header")
            fields = line.decode("ascii").split()
            if not fields:
                continue
            if fields == ["end_header"]:
                break
            if fields[0] == "format":
                binary = fields == ["format", "binary_little_endian", "1.0"]
            elif fields[0] == "element":
                if len(fields) != 3:
                    raise ValueError("Malformed PLY element")
                elements.append(fields[1])
                in_vertex = fields[1] == "vertex"
                if in_vertex:
                    if count is not None:
                        raise ValueError("Duplicate vertex element")
                    count = int(fields[2])
            elif in_vertex and fields[0] == "property":
                if len(fields) != 3 or fields[1] not in PLY_TYPES:
                    raise ValueError("PLY vertices require supported scalar properties")
                properties.append((fields[2], PLY_TYPES[fields[1]]))
        else:
            raise ValueError("PLY header exceeds 4096 lines")
        if not binary or not elements or elements[0] != "vertex":
            raise ValueError("Requires binary_little_endian PLY with vertex first")
        if count is None or not 1 <= count <= max_points or not properties:
            raise ValueError("PLY vertex count exceeds resource limit or is empty")
        if len(properties) > 128 or len({name for name, _ in properties}) != len(
            properties
        ):
            raise ValueError("Duplicate or excessive PLY properties")
        dtype = np.dtype(properties)
        size = count * dtype.itemsize
        if size > MAX_VERTEX_BYTES:
            raise ValueError("PLY vertex table exceeds byte limit")
        if size > path.stat().st_size - stream.tell():
            raise ValueError("Truncated PLY vertex table")
        table = np.fromfile(stream, dtype=dtype, count=count)
    if len(table) != count:
        raise ValueError("Truncated PLY vertex table")
    return {name: table[name] for name, _ in properties}


def decode_vertices(properties):
    """Validate all appearance data before authoring any USD files."""

    def columns(names):
        if any(name not in properties for name in names):
            raise ValueError(f"Missing required PLY properties: {names}")
        value = finite_array(
            np.column_stack([properties[name] for name in names]), str(names)
        )
        with np.errstate(over="ignore"):
            result = value.astype(np.float32)
        if not np.isfinite(result).all():
            raise ValueError("PLY data exceeds USD float32 range")
        return np.ascontiguousarray(result)

    points = columns(["x", "y", "z"])
    if not any(
        k.startswith(("f_dc_", "f_rest_", "scale_", "rot_")) or k == "opacity"
        for k in properties
    ):
        colors = columns(["red", "green", "blue"])
        if any(
            properties[k].dtype.kind != "u" or properties[k].dtype.itemsize != 1
            for k in ["red", "green", "blue"]
        ):
            raise ValueError("RGB point clouds require uint8 red/green/blue")
        return {"kind": "rgb_points", "points": points, "colors": colors / 255.0}
    rotations = columns([f"rot_{i}" for i in range(4)])
    norms = np.linalg.norm(rotations.astype(np.float64), axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError("Zero-length Gaussian quaternion")
    rotations = (rotations / norms).astype(np.float32)
    log_scales = columns([f"scale_{i}" for i in range(3)])
    with np.errstate(over="ignore", under="ignore"):
        scales = np.exp(log_scales).astype(np.float32)
    if not np.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError("Gaussian scales must be positive finite float32 after exp")
    logits = columns(["opacity"])[:, 0]
    decay = np.exp(-np.abs(logits))
    opacities = np.where(logits >= 0, 1 / (1 + decay), decay / (1 + decay)).astype(
        np.float32
    )
    dc = columns([f"f_dc_{i}" for i in range(3)])
    rest_names = [name for name in properties if name.startswith("f_rest_")]
    count = len(rest_names)
    degree = int(round(np.sqrt(count / 3 + 1))) - 1
    coefficients = (degree + 1) ** 2
    if degree not in range(4) or count != 3 * (coefficients - 1):
        raise ValueError("Only complete SH degrees 0..3 are supported")
    if count:
        rest = columns([f"f_rest_{i}" for i in range(count)])
        rest = rest.reshape(len(points), 3, coefficients - 1).transpose(0, 2, 1)
        sh = np.concatenate([dc[:, None, :], rest], axis=1).reshape(-1, 3)
    else:
        sh = dc
    return dict(
        kind="gaussian_splats",
        points=points,
        rotations=rotations,
        scales=scales,
        opacities=opacities,
        sh=np.ascontiguousarray(sh),
        sh_degree=degree,
    )


def write_appearance(data, output, source_to_world, point_width=0.02):
    """Preserve the SH frame by applying Sim3 on the parent USD Xform."""
    from pxr import Gf, Usd, UsdGeom, UsdVol, Vt

    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    transform = validate_sim3(source_to_world)
    stage = Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/Appearance")
    stage.SetDefaultPrim(root.GetPrim())
    source = UsdGeom.Xform.Define(stage, "/Appearance/Source")
    source.AddTransformOp().Set(Gf.Matrix4d(transform.T.tolist()))
    points = data["points"]
    if data["kind"] == "gaussian_splats":
        if not hasattr(UsdVol, "ParticleField3DGaussianSplat"):
            raise RuntimeError(
                "Gaussian appearance requires OpenUSD ParticleField3DGaussianSplat"
            )
        shape = UsdVol.ParticleField3DGaussianSplat.Define(
            stage, "/Appearance/Source/Splats"
        )
        shape.CreatePositionsAttr(Vt.Vec3fArray.FromNumpy(points))
        shape.CreateOrientationsAttr(
            Vt.QuatfArray([Gf.Quatf(*map(float, q)) for q in data["rotations"]])
        )
        shape.CreateScalesAttr(Vt.Vec3fArray.FromNumpy(data["scales"]))
        shape.CreateOpacitiesAttr(Vt.FloatArray.FromNumpy(data["opacities"]))
        attr = shape.CreateRadianceSphericalHarmonicsCoefficientsAttr(
            Vt.Vec3fArray.FromNumpy(data["sh"])
        )
        attr.SetMetadata("elementSize", (data["sh_degree"] + 1) ** 2)
        shape.CreateRadianceSphericalHarmonicsDegreeAttr(data["sh_degree"])
        shape.CreateProjectionModeHintAttr("perspective")
        shape.CreateSortingModeHintAttr("cameraDistance")
        # A 3-sigma sphere encloses each oriented anisotropic Gaussian's axes.
        radius = 3 * data["scales"].max(axis=1, keepdims=True)
        low, high = (points - radius).min(0), (points + radius).max(0)
    else:
        shape = UsdGeom.Points.Define(stage, "/Appearance/Source/Points")
        shape.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
        shape.CreateDisplayColorAttr(Vt.Vec3fArray.FromNumpy(data["colors"]))
        shape.CreateWidthsAttr([point_width])
        shape.SetWidthsInterpolation("constant")
        low, high = points.min(0) - point_width / 2, points.max(0) + point_width / 2
    if not np.isfinite([low, high]).all():
        raise ValueError("Appearance extent exceeds float32 range")
    shape.CreateExtentAttr([Gf.Vec3f(*map(float, low)), Gf.Vec3f(*map(float, high))])
    stage.GetRootLayer().Save()
    return {
        "kind": data["kind"],
        "count": len(points),
        "sh_degree": data.get("sh_degree"),
        "encoding": "3DGS log-scale/logit/wxyz/channel-major-SH"
        if data["kind"] == "gaussian_splats"
        else "uint8 RGB point cloud",
        "surface_mesh_generated": False,
        "texture_baked": False,
    }
