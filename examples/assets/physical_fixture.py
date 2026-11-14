"""Create public CPU inputs for inspecting five classes of physical assets.

The geometry, labels and physical choices are synthetic. No segmentation model,
reconstruction service, measurement or physics rollout is represented by this
fixture. It needs only the Python standard library to create the inputs.
"""

import argparse
import json
import struct
from pathlib import Path
from typing import Any


def _physics(
    static: float,
    dynamic: float,
    restitution: float,
    *,
    motion: str = "static",
    mass: float | None = None,
) -> dict[str, Any]:
    contact = {
        "static_friction": static,
        "dynamic_friction": dynamic,
        "restitution": restitution,
    }
    result: dict[str, Any] = {"motion_type": motion, "contact": contact}
    result["provenance"] = {
        "contact." + key: {
            "source_kind": "user_declared",
            "source": "Synthetic public fixture choice; not a physical measurement",
            "unit": "1",
            "interval": None,
        }
        for key in contact
    }
    if mass is not None:
        result["mass_kg"] = mass
        result["provenance"]["mass_kg"] = {
            "source_kind": "user_declared",
            "source": "Synthetic public fixture choice; not a physical measurement",
            "unit": "kg",
            "interval": [mass * 0.8, mass * 1.2],
        }
    return result


def create_fixture(directory: str | Path) -> dict[str, str]:
    """Write a small RGB point cloud and explicit SI object specification."""
    output = Path(directory).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("Fixture requires a fresh or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    definitions = [
        (
            "floor",
            [-3, -2, -0.1],
            [3, 2, 0],
            [0.5, 0.5, 0.5],
            _physics(0.95, 0.8, 0.02),
        ),
        (
            "cabinet",
            [-2.0, 0.5, 0],
            [-1.2, 1.2, 1.8],
            [0.2, 0.4, 0.6],
            _physics(0.45, 0.3, 0.05),
        ),
        (
            "table",
            [0.1, 0.4, 0.7],
            [1.3, 1.3, 0.85],
            [0.6, 0.4, 0.2],
            _physics(0.6, 0.5, 0.1, motion="kinematic", mass=16),
        ),
        (
            "chair",
            [0.3, -0.8, 0.05],
            [0.8, -0.3, 0.65],
            [0.3, 0.7, 0.3],
            _physics(0.7, 0.55, 0.15, motion="dynamic", mass=8),
        ),
    ]
    objects = [
        {
            "id": name,
            "type": "box",
            "category": name,
            "bounds_world": {"min": low, "max": high},
            "color": color,
            "evidence": "Synthetic axis-aligned fixture geometry, not a reconstructed real object",
            "physics": physics,
        }
        for name, low, high, color, physics in definitions
    ]
    objects[-1]["physics"].update(
        center_of_mass_m=[0, 0, 0], inertia_kg_m2=[0.4, 0.4, 0.3]
    )
    objects.append(
        {
            "id": "button",
            "type": "button",
            "category": "button",
            "axis": "Y",
            "position_world": [-1.6, 0.47, 1.2],
            "radius": 0.025,
            "travel": 0.006,
            "stiffness": 500,
            "damping": 5,
            "max_force": 12,
            "color": [0.8, 0.1, 0.05],
            "evidence": "Synthetic mechanical cap; no electrical function is inferred",
            "physics": _physics(0.8, 0.6, 0.0, motion="dynamic", mass=0.035),
        }
    )
    spec = {
        "schema_version": 1,
        "source_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "scale_provenance": {
            "status": "verified",
            "method": "synthetic_known_SI_coordinates",
            "evidence": "Public generated fixture, not a real-scene scale measurement",
        },
        "collision": {"enabled": False},
        "objects": objects,
    }
    spec_path = output / "scene_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    points = [
        (x / 2, y / 2, 0.0, 120, 120, 120) for x in range(-6, 7) for y in range(-4, 5)
    ]
    for _, low, high, color, _ in definitions[1:]:
        rgb = [round(c * 255) for c in color]
        points.extend(
            (x, y, z, *rgb)
            for x in (low[0], high[0])
            for y in (low[1], high[1])
            for z in (low[2], high[2])
        )
    cloud_path = output / "source.ply"
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    with cloud_path.open("wb") as stream:
        stream.write(header.encode("ascii"))
        for point in points:
            stream.write(struct.pack("<fffBBB", *point))
    manifest = {
        "schema_version": 1,
        "data_kind": "synthetic_fixture",
        "purpose": "CPU object material, mass, collision and evidence assignment validation",
        "classes": [obj["category"] for obj in objects],
        "source": "source.ply",
        "scene_spec": "scene_spec.json",
        "semantic_model_executed": False,
        "reconstruction_model_executed": False,
        "real_world_parameter_accuracy": "NOT_EVALUATED",
        "dynamic_response": "NOT_EVALUATED",
        "note": "Box proxies intentionally simplify shape; physical values are arbitrary test choices.",
    }
    (output / "fixture_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "source": str(cloud_path),
        "scene_spec": str(spec_path),
        "manifest": str(output / "fixture_manifest.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(create_fixture(args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
