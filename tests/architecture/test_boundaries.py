import copy
import shutil

import pytest

from scripts.check_architecture import check_architecture
from simready.core.artifacts import read_json
from tests.paths import REPOSITORY_ROOT

RULES = read_json(REPOSITORY_ROOT / "configs/architecture/module_boundaries.json")
SOURCE = REPOSITORY_ROOT / RULES["source_directory"]


def test_repository_obeys_declared_architecture():
    assert check_architecture(SOURCE, RULES) == []


@pytest.mark.parametrize(
    "module,statement,expected",
    [
        (
            "core/artifacts.py",
            "from simready.workflows import run_repair_workflow",
            "forbidden dependency core -> workflows",
        ),
        (
            "evaluation/protocol.py",
            "from simready.repair import RepairPluginRegistry",
            "forbidden dependency evaluation -> repair",
        ),
        (
            "agents/contracts.py",
            "from legacy.gaussian_repair import render",
            "forbidden runtime import legacy",
        ),
        (
            "core/artifacts.py",
            "import simready_bridge",
            "forbidden runtime import simready_bridge",
        ),
        (
            "core/artifacts.py",
            'import importlib as loader; loader.import_module("simready.workflows.video_pipeline")',
            "forbidden dependency core -> workflows",
        ),
        (
            "evaluation/protocol.py",
            'from importlib import import_module as load; load(name="simready.repair.planning")',
            "forbidden dependency evaluation -> repair",
        ),
        (
            "core/artifacts.py",
            '__import__("legacy.gaussian_repair")',
            "forbidden runtime import legacy",
        ),
        (
            "core/artifacts.py",
            "import importlib; importlib.import_module(requested_module)",
            "dynamic import requires a declared CLI router",
        ),
        (
            "assets/physics/physical_spec.py",
            "from simready.simulation import physical_spec",
            "forbidden dependency assets -> simulation",
        ),
        (
            "assets/association/semantic_projection.py",
            "from simready.workflows import semantic_assets",
            "forbidden dependency assets -> workflows",
        ),
        (
            "assets/compilation/scene_preparation.py",
            "from simready.evaluation import physical_assets",
            "forbidden dependency assets -> evaluation",
        ),
        (
            "geometry/depth_projection.py",
            "from simready.assets.appearance import gaussian_asset",
            "forbidden dependency geometry -> assets",
        ),
    ],
)
def test_checker_rejects_boundary_regressions(tmp_path, module, statement, expected):
    source = tmp_path / "simready"
    shutil.copytree(SOURCE, source, ignore=shutil.ignore_patterns("__pycache__"))
    with (source / module).open("a") as stream:
        stream.write("\n" + statement + "\n")
    assert any(expected in error for error in check_architecture(source, RULES))


def test_checker_rejects_cycles_in_declared_rules():
    rules = copy.deepcopy(RULES)
    rules["domains"]["core"]["allowed_dependencies"] = ["workflows"]
    assert any("cycle" in error for error in check_architecture(SOURCE, rules))


def test_checker_rejects_missing_public_contract(tmp_path):
    source = tmp_path / "simready"
    shutil.copytree(SOURCE, source, ignore=shutil.ignore_patterns("__pycache__"))
    path = source / "workflows/repair_loop.py"
    path.write_text(path.read_text().replace("config: JsonObject", "config", 1))
    assert any(
        "public signature" in error for error in check_architecture(source, RULES)
    )
