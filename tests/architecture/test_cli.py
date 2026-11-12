import json
import os
import subprocess
import sys

import pytest

from examples.repair_workflow.run_learning_demo import prepare
from tests.paths import REPOSITORY_ROOT


@pytest.mark.parametrize(
    "domain",
    [
        "ingestion",
        "workflows",
        "agents",
        "repair",
        "execution",
        "evaluation",
        "experience",
    ],
)
def test_domain_help_without_optional_models_or_numeric_dependencies(domain, tmp_path):
    program = """
import importlib.abc,sys
class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'numpy','scipy','PIL','torch','isaacsim','isaaclab'}:
            raise AssertionError('Help imported optional runtime: '+fullname)
sys.meta_path.insert(0,BlockHeavy())
from simready.cli import main
main([sys.argv[1],'--help'])
"""
    result = subprocess.run(
        [sys.executable, "-c", program, domain],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize(
    "command",
    ["simready-infra", "simready-benchmark", "simready-pipeline", "simready-ingest"],
)
def test_legacy_console_help_still_delegates(command, tmp_path):
    executable = os.path.join(os.path.dirname(sys.executable), command)
    result = subprocess.run(
        [executable, "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_canonical_workflow_cli_from_an_unrelated_directory(tmp_path):
    _, base = prepare(tmp_path / "inputs", 0)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready",
            "workflows",
            "run",
            "--config",
            str(base / "workflow.json"),
            "--out",
            str(tmp_path / "run"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert (
        report["status"] == "DEVELOPMENT_PASS" and report["release_authorized"] is False
    )


def test_single_package_is_imported_from_src_layout():
    import simready

    assert str(REPOSITORY_ROOT / "simready_bridge") not in simready.__file__
    assert simready.__version__ == "0.4.0"
