"""Repair-workflow commands. Provider and skill administration live elsewhere."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from simready.core.artifacts import read_json
from simready.workflows.repair_loop import run_repair_workflow


def main(argv: Sequence[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "baseline-matrix":
        from simready.workflows.baseline_matrix import main as matrix_main

        return matrix_main(argv[1:])
    if argv and argv[0] == "cup-robustness":
        from simready.workflows.cup_robustness import main as robustness_main

        return robustness_main(argv[1:])
    if argv and argv[0] == "semantic-assets":
        from simready.workflows.semantic_assets import main as assets_main

        return assets_main(argv[1:])
    if argv and argv[0] == "scene":
        from simready.workflows.scene_delivery import main as scene_main

        return scene_main(argv[1:])
    parser = argparse.ArgumentParser(prog="simready workflows", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "baseline-matrix",
        help="Frozen reconstruction backend comparisons with explicit evidence scope",
    )
    commands.add_parser(
        "cup-robustness",
        help="Frozen mass/contact randomization with independent task audits",
    )
    commands.add_parser("scene", help="Reproduce a source-bound prepared scene")
    commands.add_parser(
        "semantic-assets",
        help="Calibrated semantic masks to per-object physics and USD audit",
    )
    command = commands.add_parser("run", help="Run a bounded repair workflow")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    performance = commands.add_parser(
        "optimize-runtime", help="Run a frozen performance benchmark and bounded RSI"
    )
    performance.add_argument("--protocol", type=Path, required=True)
    performance.add_argument("--out", type=Path, required=True)
    performance.add_argument("--memory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "optimize-runtime":
            from simready.workflows.performance_loop import run_performance_workflow

            result = run_performance_workflow(args.protocol, args.out, args.memory)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "COMPLETE" else 2
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
