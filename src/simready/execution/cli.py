import argparse
import json
from pathlib import Path

from simready.core.artifacts import read_json
from simready.execution.task_graph import run_task_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute a bounded, resource-aware scene DAG (Linux)."
    )
    command = parser.add_subparsers(dest="command", required=True).add_parser("run")
    command.add_argument("--plan", required=True, type=Path)
    command.add_argument("--inventory", required=True, type=Path)
    command.add_argument("--out", required=True, type=Path)
    command.add_argument("--cache", required=True, type=Path)
    command.add_argument("--lock-dir", default="/tmp/simready-device-leases", type=Path)
    command.add_argument("--max-workers", default=8, type=int)
    args = parser.parse_args(argv)
    try:
        result = run_task_graph(
            read_json(args.plan),
            read_json(args.inventory),
            args.plan.resolve().parent,
            args.out,
            args.cache,
            args.lock_dir,
            args.max_workers,
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("status", "wall_seconds", "cache_hits", "release_authorized")
            }
        )
    )
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
