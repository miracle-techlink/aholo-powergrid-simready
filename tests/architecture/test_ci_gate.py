import json
import os
import subprocess
import sys

import pytest

from scripts.check_ci_gate import require_success
from tests.paths import REPOSITORY_ROOT


def success():
    return {name: {"result": "success"} for name in ("static", "test", "package")}


def test_all_required_checks_must_pass():
    require_success(success())


@pytest.mark.parametrize("job", ["static", "test", "package"])
@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped", None])
def test_incomplete_or_failed_dependency_blocks_merge(job, status):
    results = success()
    results[job]["result"] = status
    with pytest.raises(ValueError, match="did not succeed"):
        require_success(results)


@pytest.mark.parametrize("mutation", ["missing", "extra", "empty", "malformed"])
def test_changed_dependency_contract_blocks_merge(mutation):
    results = success()
    if mutation == "missing":
        del results["test"]
    elif mutation == "extra":
        results["unregistered"] = {"result": "success"}
    elif mutation == "empty":
        results = {}
    else:
        results["static"] = "success"
    with pytest.raises(ValueError):
        require_success(results)


@pytest.mark.parametrize("payload", [None, "invalid-json", "[]", "{}"])
def test_cli_fails_closed_when_environment_is_missing_or_invalid(payload):
    environment = dict(os.environ)
    environment.pop("CI_NEEDS", None)
    if payload is not None:
        environment["CI_NEEDS"] = payload
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts/check_ci_gate.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1 and "Merge gate failed" in result.stderr


def test_cli_reports_success_only_with_complete_successful_dependencies():
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts/check_ci_gate.py")],
        env={**os.environ, "CI_NEEDS": json.dumps(success())},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
