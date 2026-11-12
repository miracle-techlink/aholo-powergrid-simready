"""Repair-workflow commands. Provider and skill administration live elsewhere."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from simready.core.artifacts import read_json
from simready.workflows.repair_loop import run_repair_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simready workflows", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Run a bounded repair workflow")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_repair_workflow(
            read_json(args.config),
            base_dir=args.config.resolve().parent,
            output_dir=args.out,
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "DEVELOPMENT_PASS" else 2
