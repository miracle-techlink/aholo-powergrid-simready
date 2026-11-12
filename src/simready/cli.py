"""Single CLI entry point; each domain owns its arguments and implementation."""

import argparse
import importlib
from collections.abc import Sequence

COMMANDS = {
    "run": (
        "simready.workflows.scene_pipeline",
        "Run one reviewed scene from a prepared bundle or asset specification",
    ),
    "simulation": (
        "simready.simulation.cli",
        "Scene bundles, Isaac workers and contact audits",
    ),
    "ingestion": ("simready.ingestion.cli", "Extract and filter video observations"),
    "workflows": ("simready.workflows.cli", "Run an observe–repair–verify workflow"),
    "agents": ("simready.agents.cli", "Inspect decision-provider availability"),
    "repair": ("simready.repair.cli", "Plan repairs from an adapter catalog"),
    "execution": ("simready.execution.cli", "Execute a resource-bounded task graph"),
    "evaluation": (
        "simready.evaluation.cli",
        "Evaluate a candidate under a frozen protocol",
    ),
    "experience": (
        "simready.experience.cli",
        "Inspect, promote or retire verified skills",
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
