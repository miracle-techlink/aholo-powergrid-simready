"""Public synthetic mask/projection fixture; no model or real-scene claim."""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from simready.core.artifacts import digest, object_digest, write_json
from simready.geometry.cameras import look_at_c2w
from simready.ingestion.semantic_segmentation import import_segmentation


def create_fixture(output: Path) -> dict[str, Path]:
    """Generate known 2D regions and aligned RGB PLY centers for plumbing tests."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    width, height = 80, 60
    rgb = np.full((height, width, 3), 25, dtype=np.uint8)
    depth = np.zeros((height, width))
    regions = [
        ("cabinet", [4, 5, 28, 48], [60, 120, 180], 5.0),
        ("table", [42, 17, 75, 35], [180, 100, 40], 4.0),
        ("chair", [45, 38, 65, 53], [120, 60, 150], 3.5),
        ("floor", [0, 55, 80, 60], [120, 120, 120], 4.0),
    ]
    instances = []
    for category, (x0, y0, x1, y1), color, distance in regions:
        mask = np.zeros((height, width), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        rgb[mask > 0] = color
        depth[mask > 0] = distance
        path = output / f"{category}.png"
        Image.fromarray(mask).save(path)
        instances.append(
            {
                "asset_id": category,
                "category": category,
                "mask": {"path": path.name, "sha256": digest(path)},
            }
        )
    image_path = output / "image.png"
    Image.fromarray(rgb).save(image_path)
    source = {"sha256": digest(image_path), "width": width, "height": height}
    write_json(
        output / "import.json",
        {"schema_version": 1, "source_image": source, "instances": instances},
    )
    import_segmentation(image_path, output / "import.json", output / "segmentation")
    intrinsics = np.array(
        [[65.0, 0, (width - 1) / 2], [0, 65.0, (height - 1) / 2], [0, 0, 1]]
    )
    camera = look_at_c2w([0, -4, 2], [0, 1, 1.5])
    ys, xs = np.nonzero(depth)
    pixels = np.column_stack([xs, ys, np.ones(len(xs))])
    local = pixels @ np.linalg.inv(intrinsics).T * depth[ys, xs, None]
    world = local @ camera[:3, :3].T + camera[:3, 3]
    table = np.empty(
        len(world),
        dtype=[(v, "<f4") for v in "xyz"]
        + [(v, "u1") for v in ("red", "green", "blue")],
    )
    for i, key in enumerate("xyz"):
        table[key] = world[:, i]
    for i, key in enumerate(("red", "green", "blue")):
        table[key] = rgb[ys, xs, i]
    header = (
        "ply\nformat binary_little_endian 1.0\nelement vertex " + str(len(world)) + "\n"
    )
    header += "".join(f"property float {key}\n" for key in "xyz")
    header += (
        "".join(f"property uchar {key}\n" for key in ("red", "green", "blue"))
        + "end_header\n"
    )
    ply = output / "source.ply"
    ply.write_bytes(header.encode() + table.tobytes())
    transform = np.eye(4).tolist()
    write_json(
        output / "scene_spec.json",
        {
            "schema_version": 1,
            "source_to_world": transform,
            "scale_provenance": {
                "status": "unverified",
                "method": "synthetic projection fixture",
                "evidence": "Generated coordinates; not real-world measurement",
            },
            "collision": {"enabled": False},
        },
    )
    write_json(
        output / "calibration.json",
        {
            "schema_version": 1,
            "camera_convention": "opencv_c2w",
            "distortion": "none",
            "image_size": [width, height],
            "intrinsics": intrinsics.tolist(),
            "camera_to_world": camera.tolist(),
            "image_sha256": source["sha256"],
            "ply_sha256": digest(ply),
            "source_to_world_sha256": object_digest(transform),
            "evidence": "Analytic synthetic pinhole fixture. This is a plumbing test, not semantic benchmark data.",
        },
    )
    return {
        "ply": ply,
        "calibration": output / "calibration.json",
        "scene_spec": output / "scene_spec.json",
        "segmentation_manifest": output / "segmentation" / "manifest.json",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    create_fixture(parser.parse_args().out)
