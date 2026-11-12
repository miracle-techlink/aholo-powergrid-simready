"""python -m simready_bridge.benchmark run|worker|plan"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ..artifacts import digest, write_json
from .contracts import read_json
from .evaluator import evaluate
from .scheduler import plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--protocol", type=Path, required=True)
        command.add_argument("--candidate", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--expected-protocol")
        command.add_argument("--expected-candidate")
    command = commands.add_parser("plan")
    command.add_argument("--defects", type=Path, required=True)
    command.add_argument("--registry", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--round-index", type=int, default=0)
    command.add_argument("--max-rounds", type=int, default=4)
    command.add_argument("--max-actions", type=int, default=4)
    command.add_argument("--stagnant-rounds", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == "run":
            # New interpreter; no planner state or supplied scores are accepted.
            command = [
                sys.executable,
                "-m",
                "simready_bridge.benchmark",
                "worker",
                "--protocol",
                str(args.protocol.resolve()),
                "--candidate",
                str(args.candidate.resolve()),
                "--out",
                str(args.out.resolve()),
                "--expected-protocol",
                args.expected_protocol or digest(args.protocol),
                "--expected-candidate",
                args.expected_candidate or digest(args.candidate),
            ]
            return subprocess.run(command, check=False).returncode
        if args.command == "worker":
            result = evaluate(
                args.protocol,
                args.candidate,
                args.out,
                args.expected_protocol,
                args.expected_candidate,
            )
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "candidate_id",
                            "status",
                            "tracks",
                            "blocking_checks",
                            "release_authorized",
                        )
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if result["status"] == "PASS" else 2
        result = plan(
            read_json(args.defects),
            read_json(args.registry),
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
        print(
            json.dumps(
                {"status": "INVALID", "error": str(error), "release_authorized": False}
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
