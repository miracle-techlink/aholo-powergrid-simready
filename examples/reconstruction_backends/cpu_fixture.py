"""Synthetic interface fixture, not a reconstruction model or accuracy baseline."""

import argparse
import hashlib
import json
import struct
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    properties = [
        "x",
        "y",
        "z",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
        "opacity",
    ]
    header = (
        "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
        + "".join(f"property float {name}\n" for name in properties)
        + "end_header\n"
    )
    payload = header.encode() + struct.pack(
        "<14f", 0, 0, 0, 0, 0, 0, -3, -3, -3, 1, 0, 0, 0, 0
    )
    (args.out / "synthetic.ply").write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "status": "COMPLETE",
        "source_video_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
        "provider": "synthetic_cpu_fixture",
        "artifacts": [
            {
                "kind": "ply",
                "path": "synthetic.ply",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        ],
    }
    (args.out / "reconstruction.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
