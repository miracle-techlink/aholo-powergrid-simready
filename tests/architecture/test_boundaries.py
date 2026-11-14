import copy
import shutil
import subprocess
import sys

import pytest

from scripts.check_architecture import check_architecture
from simready.core.artifacts import read_json
from tests.paths import REPOSITORY_ROOT

RULES = read_json(REPOSITORY_ROOT / "configs/architecture/module_boundaries.json")
SOURCE = REPOSITORY_ROOT / RULES["source_directory"]


def test_low_level_process_import_does_not_initialize_task_orchestration():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import simready.execution.processes.local; "
            "assert 'simready.execution.tasks.graph' not in sys.modules; "
            "import simready.execution as execution; "
            "from simready.execution.tasks.graph import run_task_graph; "
            "assert execution.run_task_graph is run_task_graph",
        ],
        check=True,
        timeout=30,
    )


def test_repository_obeys_declared_architecture():
    assert check_architecture(SOURCE, RULES) == []


@pytest.mark.parametrize(
    "module,statement,expected",
    [
        (
            "core/artifacts.py",
            "from simready.workflows import video_pipeline",
            "forbidden dependency core -> workflows",
        ),
        (
            "evaluation/protocol.py",
            "from simready.repair import RepairPluginRegistry",
            "forbidden dependency evaluation -> repair",
        ),
        (
            "ingestion/video_frames.py",
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
            'import importlib as loader; loader.import_module("simready.workflows.pipelines.video_pipeline")',
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
    path = source / "workflows/pipelines/semantic_assets.py"
    path.write_text(path.read_text().replace("ply: str | Path", "ply", 1))
    assert any(
        "public signature" in error for error in check_architecture(source, RULES)
    )


@pytest.mark.parametrize(
    "module,statement,expected",
    [
        (
            "execution/resources/resource_pool.py",
            "from simready.execution.tasks.graph import run_task_graph",
            "forbidden submodule dependency execution.resources -> execution.tasks",
        ),
        (
            "execution/processes/local.py",
            "from simready.execution.workers import isaac_launcher",
            "forbidden submodule dependency execution.processes -> execution.workers",
        ),
        (
            "workflows/contracts/validation.py",
            "from simready.workflows.planning import pipeline_spec",
            "forbidden submodule dependency workflows.contracts -> workflows.planning",
        ),
        (
            "workflows/stages/semantic_stage.py",
            'import importlib; importlib.import_module("simready.workflows.pipelines.video_pipeline")',
            "forbidden submodule dependency workflows.stages -> workflows.pipelines",
        ),
        (
            "evaluation/inspection/comparison.py",
            "from simready.evaluation.inspection import physical_checks",
            "Module dependency cycle",
        ),
    ],
)
def test_checker_rejects_internal_dependency_regressions(
    tmp_path, module, statement, expected
):
    source = tmp_path / "simready"
    shutil.copytree(SOURCE, source, ignore=shutil.ignore_patterns("__pycache__"))
    with (source / module).open("a") as stream:
        stream.write("\ndef regression_probe():\n    " + statement + "\n")
    assert any(expected in error for error in check_architecture(source, RULES))


def test_checker_rejects_submodule_rule_cycle():
    rules = copy.deepcopy(RULES)
    rules["domains"]["execution"]["layers"]["resources"] = ["tasks"]
    assert any(
        "Submodule dependency in execution cycle" in error
        for error in check_architecture(SOURCE, rules)
    )


def test_checker_rejects_unclassified_flat_module(tmp_path):
    source = tmp_path / "simready"
    shutil.copytree(SOURCE, source, ignore=shutil.ignore_patterns("__pycache__"))
    (source / "execution/unclassified.py").write_text('"""Unowned runtime code."""\n')
    assert any(
        "execution: unclassified root modules" in error
        for error in check_architecture(source, RULES)
    )
