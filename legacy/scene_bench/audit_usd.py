#!/usr/bin/env python3
"""CPU-only USD/Gaussian internal-validity audit; no SimulationApp or renderer.

Example (Isaac venv Python supplies pxr):
  python audit_usd.py --scene captured=/path/scene.usda \
    --scene generated=/path/scene.usdc --out /path/usd_audit.json

Only local dependencies are opened. Remote dependencies block stage composition.
Array scans use zero-copy Vt buffers and bounded NumPy chunks. USD itself may
materialize an entire authored array; this is not a constant-memory USD reader.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_key, "1")

import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdVol


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def external_uri(value):
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)) or value.startswith("//")


def local_dependencies(root):
    """Preflight local layer arcs and authored asset paths before composition."""
    pending = [root]
    visited, records, edges, errors = set(), [], [], []
    while pending:
        path = pending.pop().resolve()
        if str(path) in visited:
            continue
        visited.add(str(path))
        if not path.is_file():
            records.append({"path": str(path), "exists": False})
            errors.append("Missing dependency: " + str(path))
            continue
        record = {
            "path": str(path),
            "exists": True,
            "bytes": path.stat().st_size,
            "sha256": checksum(path),
        }
        records.append(record)
        if path.suffix.lower() not in {".usd", ".usda", ".usdc"}:
            continue
        try:
            layer = Sdf.Layer.FindOrOpen(str(path))
            if not layer:
                raise ValueError("Sdf could not open layer")
            record["sdf_layer_opened"] = True
            refs = set(layer.GetExternalReferences())

            def inspect_asset_property(spec_path):
                spec = layer.GetObjectAtPath(spec_path)
                if not isinstance(spec, Sdf.AttributeSpec):
                    return
                if spec.typeName not in (
                    Sdf.ValueTypeNames.Asset,
                    Sdf.ValueTypeNames.AssetArray,
                ):
                    return
                values = [spec.default]
                values.extend(
                    layer.QueryTimeSample(spec_path, t)
                    for t in layer.ListTimeSamplesForPath(spec_path)
                )
                for value in values:
                    if value is None:
                        continue
                    for asset in [value] if isinstance(value, Sdf.AssetPath) else value:
                        if asset.path:
                            refs.add(asset.path)

            layer.Traverse(Sdf.Path.absoluteRootPath, inspect_asset_property)
            for ref in sorted(refs):
                edge = {"from": str(path), "authored_path": ref}
                if external_uri(ref) or "[" in ref or "]" in ref:
                    edge["status"] = "not_accessed_remote_or_package_reference"
                    errors.append("Unsupported dependency, not accessed: " + ref)
                else:
                    resolved = (path.parent / ref).resolve()
                    edge.update(resolved=str(resolved), status="local")
                    pending.append(resolved)
                edges.append(edge)
        except Exception as exc:
            errors.append(
                f"Layer inspection failed {path}: {type(exc).__name__}: {exc}"
            )
    return {
        "files": records,
        "edges": edges,
        "errors": errors,
        "network_access": False,
        "all_dependencies_local_and_present": not errors,
    }


def scan_array(attribute, chunk_rows, *, transform=None):
    value = attribute.Get(Usd.TimeCode.Default())
    if value is None:
        return {"present": False}, None
    array = np.asarray(memoryview(value))  # Vt buffer, no full-array NumPy copy.
    result = {
        "present": True,
        "count": len(value),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "finite": True,
        "nonfinite_values": 0,
        "time_samples_not_audited": attribute.GetTimeSamples(),
        "element_size": attribute.GetMetadata("elementSize"),
    }
    minimum = maximum = world_min = world_max = None
    nonpositive = below_zero = above_one = 0
    for offset in range(0, len(array), chunk_rows):
        block = array[offset : offset + chunk_rows]
        finite = np.isfinite(block)
        bad = int(finite.size - np.count_nonzero(finite))
        result["nonfinite_values"] += bad
        nonpositive += int(np.count_nonzero(block <= 0))
        below_zero += int(np.count_nonzero(block < 0))
        above_one += int(np.count_nonzero(block > 1))
        if bad:
            continue
        lo, hi = block.min(axis=0), block.max(axis=0)
        minimum = lo if minimum is None else np.minimum(minimum, lo)
        maximum = hi if maximum is None else np.maximum(maximum, hi)
        if transform is not None:
            # Gf transforms use row vectors: point*R + translation row.
            world = block @ transform[:3, :3] + transform[3, :3]
            lo, hi = world.min(axis=0), world.max(axis=0)
            world_min = lo if world_min is None else np.minimum(world_min, lo)
            world_max = hi if world_max is None else np.maximum(world_max, hi)
    result.update(
        finite=result["nonfinite_values"] == 0,
        nonpositive_values=nonpositive,
        below_zero_values=below_zero,
        above_one_values=above_one,
    )
    if minimum is not None:
        result.update(
            minimum=np.asarray(minimum).tolist(), maximum=np.asarray(maximum).tolist()
        )
    bbox = (
        None
        if world_min is None
        else {"min": world_min.tolist(), "max": world_max.tolist()}
    )
    del array, value
    return result, bbox


def audit_scene(label, path, chunk_rows):
    path = path.expanduser().resolve()
    report = {
        "label": label,
        "path": str(path),
        "scope": "USD internal validity at default time",
        "errors": [],
        "warnings": [],
        "gaussian_prims": [],
    }
    deps = local_dependencies(path)
    report["dependencies"] = deps
    report["errors"].extend(deps["errors"])
    if deps["errors"]:
        report.update(stage_opened=False, internal_validity_pass=False)
        return report
    root_record = next(x for x in deps["files"] if x["path"] == str(path))
    report.update(sha256=root_record["sha256"], bytes=root_record["bytes"])
    stage = Usd.Stage.Open(str(path))
    report["stage_opened"] = bool(stage)
    if not stage:
        report["errors"].append("Usd.Stage.Open failed")
        report["internal_validity_pass"] = False
        return report
    meters = UsdGeom.GetStageMetersPerUnit(stage)
    axis = str(UsdGeom.GetStageUpAxis(stage))
    report["coordinates"] = {
        "up_axis": axis,
        "meters_per_unit": meters if math.isfinite(meters) else None,
        "up_axis_authored": stage.HasAuthoredMetadata("upAxis"),
        "meters_per_unit_authored": stage.HasAuthoredMetadata("metersPerUnit"),
    }
    if axis not in ("Y", "Z") or not math.isfinite(meters) or meters <= 0:
        report["errors"].append("Invalid stage unit/up-axis metadata")
    report["composition_errors"] = [str(e) for e in stage.GetCompositionErrors()]
    report["errors"].extend(report["composition_errors"])
    collisions = []
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            collisions.append(str(prim.GetPath()))
        if prim.GetTypeName() != "ParticleField3DGaussianSplat":
            continue
        field = UsdVol.ParticleField3DGaussianSplat(prim)
        record = {
            "path": str(prim.GetPath()),
            "type": str(prim.GetTypeName()),
            "arrays": {},
        }
        matrix = np.asarray(xforms.GetLocalToWorldTransform(prim))
        record["world_transform_finite"] = bool(np.isfinite(matrix).all())
        if not record["world_transform_finite"]:
            report["errors"].append(f"Nonfinite world transform: {prim.GetPath()}")
        attrs = {
            "positions": field.GetPositionsAttr(),
            "scales": field.GetScalesAttr(),
            "orientations": field.GetOrientationsAttr(),
            "opacities": field.GetOpacitiesAttr(),
            "sh_coefficients": field.GetRadianceSphericalHarmonicsCoefficientsAttr(),
        }
        for name, attr in attrs.items():
            stats, world_bbox = scan_array(
                attr,
                chunk_rows,
                transform=matrix
                if name == "positions" and record["world_transform_finite"]
                else None,
            )
            record["arrays"][name] = stats
            if world_bbox is not None:
                record["world_position_centers_bbox_stage_units"] = world_bbox
            if not stats.get("present") or not stats.get("finite"):
                report["errors"].append(f"Missing/nonfinite {prim.GetPath()}.{name}")
            if stats.get("time_samples_not_audited"):
                report["warnings"].append(
                    f"{prim.GetPath()}.{name}: time samples exist; only default value audited"
                )
        arrays = record["arrays"]
        count = arrays["positions"].get("count", 0)
        record["point_count"] = count
        if count <= 0:
            report["errors"].append(f"Empty Gaussian prim: {prim.GetPath()}")
        for name in ("scales", "orientations", "opacities"):
            if arrays[name].get("count") != count:
                report["errors"].append(
                    f"Point-count mismatch: {prim.GetPath()}.{name}"
                )
        if arrays["scales"].get("nonpositive_values", 0):
            report["errors"].append(f"Nonpositive Gaussian scale: {prim.GetPath()}")
        opacity = arrays["opacities"]
        if opacity.get("below_zero_values", 0) or opacity.get("above_one_values", 0):
            report["warnings"].append(f"Opacity outside [0,1]: {prim.GetPath()}")
        degree = field.GetRadianceSphericalHarmonicsDegreeAttr().Get()
        record["sh_degree"] = degree
        expected = (
            (int(degree) + 1) ** 2 if degree is not None and int(degree) >= 0 else None
        )
        record["expected_sh_coefficients_per_point"] = expected
        if (
            expected is None
            or arrays["sh_coefficients"].get("count") != count * expected
        ):
            report["errors"].append(
                f"SH coefficient count/degree mismatch: {prim.GetPath()}"
            )
        if arrays["sh_coefficients"].get("element_size") != expected:
            report["errors"].append(f"SH elementSize mismatch: {prim.GetPath()}")
        extent = field.GetExtentAttr().Get()
        extent_finite = extent is None or bool(np.isfinite(np.asarray(extent)).all())
        record["authored_extent_local"] = (
            None if extent is None or not extent_finite else [list(v) for v in extent]
        )
        if not extent_finite:
            report["errors"].append(f"Nonfinite authored extent: {prim.GetPath()}")
        report["gaussian_prims"].append(record)
        gc.collect()
    report["gaussian_prim_count"] = len(report["gaussian_prims"])
    report["gaussian_point_count"] = sum(
        x["point_count"] for x in report["gaussian_prims"]
    )
    if not report["gaussian_prims"]:
        report["errors"].append("No Gaussian prim found")
    report["collision"] = {
        "authored_collision_api_prim_count": len(collisions),
        "prim_paths": collisions,
        "collision_usability_certified": False,
        "note": "Visual Gaussian data, valid arrays, and bbox do not establish solid geometry, collision coverage, graspability, or robot reachability.",
    }
    report["bbox_scope"] = (
        "Gaussian position-center AABB in stage units after local-to-world transform; excludes splat extent and is not a room surface or collision bound."
    )
    report["internal_validity_pass"] = not report["errors"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", action="append", required=True, metavar="LABEL=PATH")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=65536)
    args = parser.parse_args()
    if args.chunk_rows <= 0:
        parser.error("--chunk-rows must be positive")
    scenes = []
    for spec in args.scene:
        label, separator, raw = spec.partition("=")
        if not separator:
            raw, label = spec, Path(spec).stem
        try:
            scenes.append(audit_scene(label, Path(raw), args.chunk_rows))
        except Exception as exc:
            scenes.append(
                {
                    "label": label,
                    "path": str(Path(raw).resolve()),
                    "internal_validity_pass": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audit_script": str(Path(__file__).resolve()),
        "audit_script_sha256": checksum(Path(__file__)),
        "method": "CPU-only pxr; no SimulationApp, renderer, cloud requests, or source modifications",
        "chunk_rows": args.chunk_rows,
        "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / 1024,
        "memory_scope": "One USD attribute at a time; zero-copy Vt view and bounded temporary chunks. USD retains its own layer/array caches.",
        "scenes": scenes,
        "limitations": [
            "This is internal USD validity, not reconstruction fidelity or real-layout accuracy.",
            "No score is assigned to the generated layout; user rejection is not reversed by this audit.",
            "Visual Gaussian validity does not certify usable collision geometry.",
            "BBox measures splat centers; it is not a reconstruction of room surfaces.",
            "Default-time active composed prims are inspected; inactive variants and animated sample validity are not certified.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    temporary.replace(args.out)
    print(
        json.dumps(
            {
                "report": str(args.out.resolve()),
                "results": [
                    {
                        "label": s["label"],
                        "pass": s["internal_validity_pass"],
                        "points": s.get("gaussian_point_count"),
                        "errors": s.get("errors"),
                    }
                    for s in scenes
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(s["internal_validity_pass"] for s in scenes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
