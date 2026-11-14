"""Fail a required merge check if any mandatory CI job did not succeed."""

import json
import os
import sys

REQUIRED_JOBS = frozenset({"static", "test", "package"})


def require_success(results: dict) -> None:
    """Reject failures, skipped/cancelled jobs, missing jobs and malformed input."""
    if not isinstance(results, dict) or set(results) != REQUIRED_JOBS:
        raise ValueError("CI needs must contain exactly static, test and package")
    for name in sorted(REQUIRED_JOBS):
        result = results[name]
        if not isinstance(result, dict) or result.get("result") != "success":
            raise ValueError(f"Mandatory CI job {name} did not succeed: {result!r}")


def main() -> int:
    try:
        require_success(json.loads(os.environ["CI_NEEDS"]))
    except (KeyError, ValueError, TypeError) as exc:
        print(f"Merge gate failed: {exc}", file=sys.stderr)
        return 1
    print("All mandatory CI jobs succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
