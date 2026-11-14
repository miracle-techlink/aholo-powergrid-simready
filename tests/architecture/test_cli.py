import json
import subprocess
import sys

import pytest

from examples.pipeline.create_fixture import create_fixture
from tests.paths import REPOSITORY_ROOT


@pytest.mark.parametrize(
    "domain",
    [
        "run",
        "simulation",
        "ingestion",
        "workflows",
        "execution",
        "evaluation",
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


@pytest.mark.parametrize("command", ["agents", "repair", "experience"])
def test_full_branch_commands_are_not_available_on_core_main(command, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "simready", command, "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "invalid choice" in result.stderr


def test_canonical_pipeline_cli_from_an_unrelated_directory(tmp_path):
    pytest.importorskip("pxr")
    path = create_fixture(tmp_path / "inputs")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready",
            "run",
            "--pipeline",
            str(path),
            "--out",
            str(tmp_path / "run"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads((tmp_path / "run/summary.json").read_text())
    assert report["status"] == "COMPLETE"
    assert report["physical_asset_validation"] == "PASS"
    assert report["task_validated"] is False


def test_single_package_is_imported_from_src_layout():
    from importlib.metadata import version

    import simready

    assert str(REPOSITORY_ROOT / "simready_bridge") not in simready.__file__
    assert simready.__version__ == version("simready-pipeline")
