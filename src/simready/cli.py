"""Single CLI entry point; each domain owns its arguments and implementation."""

import argparse
import importlib
from collections.abc import Sequence

COMMANDS = {
    "run": (
        "simready.workflows.pipelines.entry",
        "Video reconstruction, USD compilation and independent scene validation",
    ),
    "simulation": (
        "simready.simulation.cli",
        "Isaac Sim environment, scene survey and physical probes",
    ),
    "ingestion": ("simready.ingestion.cli", "Extract and filter video observations"),
    "workflows": ("simready.workflows.cli", "Baseline comparisons and semantic assets"),
    "execution": ("simready.execution.cli", "Execute a resource-bounded task graph"),
    "evaluation": (
        "simready.evaluation.cli",
        "Evaluate a candidate under a frozen protocol",
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="simready", description="Video-to-simulation pipeline tools"
    )
    commands = parser.add_subparsers(dest="domain", required=True)
    for name, (_, description) in COMMANDS.items():
        commands.add_parser(name, help=description, add_help=False)
    args, remaining = parser.parse_known_args(argv)
    module = importlib.import_module(COMMANDS[args.domain][0])
    return module.main(remaining)
