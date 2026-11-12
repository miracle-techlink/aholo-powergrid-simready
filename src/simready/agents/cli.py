"""Inspect decision-provider availability without exporting credentials."""

import argparse
import json
from collections.abc import Sequence

from simready.agents.providers.codex_cli import CodexCliProvider


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simready agents", description=__doc__)
    parser.add_subparsers(dest="command", required=True).add_parser(
        "doctor", help="Check local Codex login status"
    )
    parser.parse_args(argv)
    result = CodexCliProvider().doctor()
    print(json.dumps(result))
    return 0 if result.get("authenticated") else 2
