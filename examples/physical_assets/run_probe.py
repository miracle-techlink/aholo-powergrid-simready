"""Launch the public synthetic response protocol in Docker or local Isaac Python."""

import argparse
import time
from pathlib import Path

from simready.core.artifacts import write_json
from simready.execution.isaac_launcher import launch_worker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backend", choices=["docker", "local"], default="docker")
    parser.add_argument(
        "--python", help="Isaac Python executable for the local backend"
    )
    parser.add_argument("--image", default="simready-isaac-core:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--accept-eula", action="store_true")
    args = parser.parse_args()
    if not args.accept_eula:
        parser.error("Pass --accept-eula after accepting the NVIDIA Isaac Sim license")
    output = args.out.resolve()
    if output.exists():
        parser.error("Select a fresh output directory to preserve prior evidence")
    repo = Path(__file__).resolve().parents[2]
    tick = time.perf_counter()
    code = None
    try:
        code = launch_worker(
            [
                "-m",
                "simready.simulation.physical_probe",
                "--out",
                "/output/probe" if args.backend == "docker" else str(output / "probe"),
            ],
            repo=repo,
            assets=repo / "examples/physical_assets",
            output=output,
            backend=args.backend,
            python=args.python,
            image=args.image,
            gpu=args.gpu,
        )
        return code
    finally:
        write_json(
            output / "launcher_timing.json",
            {
                "wall_s": time.perf_counter() - tick,
                "exit_code": code,
                "scope": "Launch preparation, worker lifetime, shutdown and container cleanup",
            },
        )


if __name__ == "__main__":
    raise SystemExit(main())
