"""Add a smooth far-room observation segment without changing robot motion."""

import argparse
import json
from pathlib import Path

import numpy as np

from simready.core.artifacts import digest, write_json


def observation_camera(rows, reference_to_world):
    def smooth(value):
        u = np.clip(value, 0.0, 1.0)
        return u**3 * (10.0 + u * (-15.0 + 6.0 * u))

    matrix = np.asarray(reference_to_world)
    eye = np.array([-2.8, 0.3, 2.3]) @ matrix[:3, :3].T + matrix[:3, 3]
    target = np.array([-2.7, -2.8, 0.1]) @ matrix[:3, :3].T + matrix[:3, 3]
    result = []
    for original in rows:
        row = dict(original)
        t = row["time_s"]
        weight = smooth((t - 6.0) / 6.0) * (1.0 - smooth((t - 18.0) / 6.0))
        for key, goal in [("eye", eye), ("target", target)]:
            row[key] = ((1.0 - weight) * np.asarray(row[key]) + weight * goal).tolist()
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory", required=True, type=Path)
    parser.add_argument("--reference-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Refusing to overwrite an existing trajectory")
    rows = json.loads(args.trajectory.read_text())
    record = json.loads(args.reference_manifest.read_text())
    result = observation_camera(rows, record["far_left"]["reference_to_world"])
    write_json(args.out, result)
    write_json(
        args.out.with_suffix(".camera.json"),
        {
            "source_sha256": digest(args.trajectory),
            "result_sha256": digest(args.out),
            "robot_motion_unchanged": all(
                a["robot_position"] == b["robot_position"] and a["yaw"] == b["yaw"]
                for a, b in zip(rows, result, strict=True)
            ),
            "camera": "Original camera at start/end; quintic transition 6–12 s into far-left view, hold 12–18 s, return 18–24 s",
        },
    )


if __name__ == "__main__":
    main()
