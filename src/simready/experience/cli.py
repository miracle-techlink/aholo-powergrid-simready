"""Skill lifecycle commands backed by the shared experience database."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from simready.experience import EpisodeStore, SkillLibrary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simready experience", description=__doc__)
    skills = parser.add_subparsers(dest="kind", required=True).add_parser("skills")
    commands = skills.add_subparsers(dest="command", required=True)
    for name in ("show", "promote", "retire"):
        command = commands.add_parser(name)
        command.add_argument(
            "--database", "--memory", dest="database", type=Path, required=True
        )
        command.add_argument("--skill-id", required=True)
    args = parser.parse_args(argv)
    try:
        library = SkillLibrary(EpisodeStore(args.database))
        action = {
            "show": library.get,
            "promote": library.promote,
            "retire": library.retire,
        }[args.command]
        print(json.dumps(action(args.skill_id), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3
