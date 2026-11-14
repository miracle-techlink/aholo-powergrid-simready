"""python -m simready.evaluation run|worker"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from simready.core.artifacts import digest


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "physical-assets":
        from simready.evaluation.inspection.physical_assets import main as physical_main

        return physical_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "physical-assets",
        help="Independently reopen USD and compare declared object physics",
    )
    for name in ("run", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--protocol", type=Path, required=True)
        command.add_argument("--candidate", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--expected-protocol")
        command.add_argument("--expected-candidate")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            # New interpreter; no planner state or supplied scores are accepted.
            command = [
                sys.executable,
                "-m",
                "simready.evaluation",
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
            from simready.evaluation.scoring import evaluate_candidate

            result = evaluate_candidate(
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
