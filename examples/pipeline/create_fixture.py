"""Portable CPU pipeline inputs; synthetic geometry is never a model baseline score."""

import argparse
import struct
import subprocess
import sys
from pathlib import Path

from simready.core.artifacts import digest, write_json
from simready.core.evidence import reference


def create_fixture(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    video = directory / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x96:rate=4",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    names = "x y z rot_0 rot_1 rot_2 rot_3 scale_0 scale_1 scale_2 opacity f_dc_0 f_dc_1 f_dc_2".split()
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    ply = directory / "gaussian.ply"
    ply.write_bytes(
        header.encode()
        + b"".join(
            struct.pack("<14f", *([x] * 3 + [1, 0, 0, 0, -3, -3, -3, 0, 0, 0, 0]))
            for x in [0, 1]
        )
    )
    manifest = directory / "reconstruction.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "source_video_sha256": digest(video),
            "artifacts": [
                {
                    "kind": "ply",
                    "path": ply.name,
                    "sha256": digest(ply),
                    "bytes": ply.stat().st_size,
                }
            ],
        },
    )
    scene = directory / "scene-spec.json"
    write_json(
        scene,
        {
            "schema_version": 1,
            "source_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
            "scale_provenance": {
                "status": "verified",
                "method": "synthetic metres",
                "evidence": "synthetic fixture, not real scale measurement",
            },
            "objects": [
                {
                    "id": "Cup",
                    "type": "box",
                    "bounds_world": {"min": [0, 0, 0], "max": [0.1, 0.1, 0.1]},
                    "physics": {"motion_type": "dynamic", "mass_kg": 0.1},
                }
            ],
        },
    )
    config = directory / "pipeline.json"
    write_json(
        config,
        {
            "schema_version": 1,
            "input": {"video": video.name},
            "stages": {
                "observations": {"provider": "video_frames", "fps": 2},
                "reconstruction": {
                    "provider": "artifact",
                    "manifest": {"path": manifest.name, "sha256": digest(manifest)},
                    "algorithm": {
                        "name": "synthetic_cpu_fixture",
                        "version": "1",
                        "implementation_sha256": digest(__file__),
                    },
                },
                "assets": {"provider": "scene_spec", "scene_spec": scene.name},
                "delivery": {"provider": "usd_scene", "mode": "prepare"},
            },
            "execution": {
                "backend": "local",
                "python": sys.executable,
                "runtime_identity": "synthetic-cpu-contract-v1",
            },
        },
    )
    write_json(
        directory / "fixture.json",
        {
            "source": reference(video),
            "geometry": reference(ply),
            "model_inference_performed": False,
            "model_quality": "NOT_EVALUATED",
        },
    )
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(create_fixture(parser.parse_args().out))
