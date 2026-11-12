"""Build a nonexecuting repair plan from the declared adapter catalog."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from simready.core.artifacts import read_json, write_json
from simready.repair.planning import plan_repairs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simready repair", description=__doc__)
    command = parser.add_subparsers(dest="command", required=True).add_parser("plan")
    command.add_argument("--defects", type=Path, required=True)
    command.add_argument(
        "--catalog", "--registry", dest="catalog", type=Path, required=True
    )
    command.add_argument("--out", type=Path, required=True)
    for name, default in (
        ("round-index", 0),
        ("max-rounds", 4),
        ("max-actions", 4),
        ("stagnant-rounds", 0),
    ):
        command.add_argument("--" + name, type=int, default=default)
    args = parser.parse_args(argv)
    try:
        result = plan_repairs(
            read_json(args.defects),
            read_json(args.catalog),
            round_index=args.round_index,
            max_rounds=args.max_rounds,
            max_actions=args.max_actions,
            stagnant_rounds=args.stagnant_rounds,
        )
        if args.out.exists():
            raise ValueError("Plan output already exists")
        write_json(args.out, result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "actions": len(result["actions"]),
                    "executed": False,
                }
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3
