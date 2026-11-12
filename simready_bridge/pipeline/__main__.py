import argparse
import json
from pathlib import Path

from ..agent.codex import CodexProvider
from ..artifacts import read_json
from ..memory import MemoryStore
from ..skills import SkillCatalog
from .runner import run_campaign


def main():
    parser = argparse.ArgumentParser(
        description="Run an evidence-bound agent repair campaign."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    commands.add_parser("doctor")
    for name in ("skill-show", "skill-promote", "skill-retire"):
        command = commands.add_parser(name)
        command.add_argument("--memory", type=Path, required=True)
        command.add_argument("--skill-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            result = run_campaign(
                read_json(args.config), base=args.config.resolve().parent, out=args.out
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "DEVELOPMENT_PASS" else 2
        if args.command == "doctor":
            result = CodexProvider().doctor()
            print(json.dumps(result))
            return 0 if result.get("authenticated") else 2
        catalog = SkillCatalog(MemoryStore(args.memory))
        action = {
            "skill-show": catalog.get,
            "skill-promote": catalog.promote,
            "skill-retire": catalog.retire,
        }[args.command]
        print(json.dumps(action(args.skill_id), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
