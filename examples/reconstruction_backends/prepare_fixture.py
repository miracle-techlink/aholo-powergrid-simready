"""Write an explicitly synthetic, fully hashed provider configuration."""

import argparse
import platform
import sys
from pathlib import Path

from simready.core.artifacts import digest, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("cpu_fixture.py").resolve()
    runtime = args.out.resolve() / "runtime.json"
    write_json(
        runtime,
        {
            "python": sys.version,
            "platform": platform.platform(),
            "model": None,
            "purpose": "synthetic_interface_fixture",
        },
    )
    write_json(
        args.out / "provider.json",
        {
            "provider": "command",
            "base_dir": str(script.parent),
            "algorithm": {
                "name": "synthetic_cpu_fixture",
                "version": "1",
                "implementation_sha256": digest(script),
            },
            "argv": [
                "{python}",
                "{input:implementation}",
                "--video",
                "{video}",
                "--out",
                "{out}",
            ],
            "input_files": {
                "implementation": {"path": str(script), "sha256": digest(script)}
            },
            "runtime_identity": {"path": str(runtime), "sha256": digest(runtime)},
            "environment": {"inherit": [], "secrets": []},
            "resources": {"kind": "cpu"},
        },
    )


if __name__ == "__main__":
    main()
