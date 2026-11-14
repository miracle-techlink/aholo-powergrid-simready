"""Prepare a CPU interface comparison; these outputs are not model reconstructions."""

import argparse
import platform
import subprocess
import sys
from pathlib import Path

from simready.core.artifacts import digest, write_json
from simready.core.evidence import reference


def prepare(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    video = output / "synthetic-video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=4:duration=1",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    implementation = (
        Path(__file__).resolve().parents[1] / "reconstruction_backends/cpu_fixture.py"
    )
    runtime = output / "runtime.json"
    write_json(
        runtime,
        {
            "python": sys.version,
            "platform": platform.platform(),
            "model": None,
            "purpose": "synthetic_interface_fixture",
        },
    )
    imported = output / "import-source"
    subprocess.run(
        [
            sys.executable,
            str(implementation),
            "--video",
            str(video),
            "--out",
            str(imported),
        ],
        check=True,
    )
    command = {
        "provider": "command",
        "base_dir": str(output),
        "algorithm": {
            "name": "synthetic_cpu_fixture",
            "version": "1",
            "implementation_sha256": digest(implementation),
        },
        "argv": [
            "{python}",
            "{input:implementation}",
            "--video",
            "{video}",
            "--out",
            "{out}",
        ],
        "input_files": {"implementation": reference(implementation)},
        "runtime_identity": reference(runtime),
        "environment": {"inherit": [], "secrets": []},
        "resources": {"kind": "cpu"},
    }
    members = []
    for name, provider in (
        ("synthetic_cpu", command),
        (
            "artifact",
            {
                "provider": "artifact",
                "manifest": reference(imported / "reconstruction.json"),
            },
        ),
    ):
        pipeline = output / f"{name}.json"
        write_json(
            pipeline,
            {
                "schema_version": 1,
                "input": {"video": str(video)},
                "stages": {"reconstruction": provider},
                "execution": {"backend": "local", "python": sys.executable},
            },
        )
        members.append({"id": name, "pipeline": reference(pipeline)})
    matrix = output / "matrix.json"
    write_json(
        matrix,
        {
            "schema_version": 1,
            "mode": "end_to_end",
            "purpose": "interface_validation",
            "video": reference(video),
            "members": members,
        },
    )
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.out))


if __name__ == "__main__":
    main()
