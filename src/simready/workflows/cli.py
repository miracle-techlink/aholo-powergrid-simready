"""Supporting workflows for the core video-to-USD pipeline."""

import argparse
import importlib
import sys
from collections.abc import Sequence

COMMANDS = {
    "baseline-matrix": "simready.workflows.pipelines.baseline_matrix",
    "semantic-assets": "simready.workflows.pipelines.semantic_assets",
}


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="simready workflows", description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    if argv and argv[0] in COMMANDS:
        return importlib.import_module(COMMANDS[argv[0]]).main(argv[1:])
    parser.parse_args(argv)
    return 0
