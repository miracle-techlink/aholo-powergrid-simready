"""Temporary 0.3 command aliases; all behavior delegates to the canonical CLI.

Python import paths are intentionally not aliased. See docs/migration.md.
"""

import sys
from collections.abc import Sequence

from simready.cli import main


def _arguments(argv: Sequence[str] | None) -> list[str]:
    return list(sys.argv[1:] if argv is None else argv)


def pipeline_main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args and args[0] == "doctor":
        return main(["agents", *args])
    if args and args[0] in ("skill-show", "skill-promote", "skill-retire"):
        return main(["experience", "skills", args[0].removeprefix("skill-"), *args[1:]])
    return main(["workflows", *args])


def infra_main(argv: Sequence[str] | None = None) -> int:
    return main(["execution", "run", *_arguments(argv)])


def benchmark_main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    return main(["repair" if args and args[0] == "plan" else "evaluation", *args])


def ingest_main(argv: Sequence[str] | None = None) -> int:
    return main(["ingestion", "extract", *_arguments(argv)])
