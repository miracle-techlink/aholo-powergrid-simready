"""Canonical imports and legacy aliases share implementations and real USD output."""

import ast
import importlib
import json
import subprocess
import sys

import pytest

from simready.core.artifacts import digest, read_json, write_json
from tests.paths import REPOSITORY_ROOT

MOVED = {
    "gaussian_asset": "assets.appearance.gaussian_asset",
    "semantic_projection": "assets.association.semantic_projection",
    "physical_spec": "assets.physics.physical_spec",
    "physical_candidate": "assets.physics.physical_candidate",
    "physical_authoring": "assets.physics.physical_authoring",
    "category_priors": "assets.physics.category_priors",
    "scene_preparation": "assets.compilation.scene_preparation",
    "depth_projection": "geometry.depth_projection",
}


@pytest.mark.parametrize("legacy,canonical", MOVED.items())
def test_compatibility_alias_has_no_second_implementation(
    legacy, canonical, monkeypatch
):
    old = importlib.import_module(f"simready.simulation.{legacy}")
    new = importlib.import_module(f"simready.{canonical}")
    assert old is new
    sentinel = object()
    monkeypatch.setattr(old, "migration_probe", sentinel, raising=False)
    assert new.migration_probe is sentinel
    tree = ast.parse(
        (REPOSITORY_ROOT / f"src/simready/simulation/{legacy}.py").read_text()
    )
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in ast.walk(tree)
    )


def test_canonical_asset_imports_do_not_load_simulation_or_workflows():
    names = [f"simready.{name}" for name in MOVED.values()]
    code = f"""
import importlib, json, sys
for name in {names!r}:
    importlib.import_module(name)
print(json.dumps([name for name in sys.modules if name.startswith((
    'simready.simulation', 'simready.workflows', 'simready.evaluation'))]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == []


def test_geometry_projection_imports_no_asset_layer():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import simready.geometry.depth_projection; "
            "assert not any(n.startswith(('simready.assets','simready.simulation')) for n in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0


def test_legacy_module_patch_reaches_canonical_preparation(tmp_path, monkeypatch):
    from simready.assets.compilation.scene_preparation import prepare_scene
    from simready.simulation import scene_preparation as legacy
    from tests.fixtures.scene_preparation import cloud, write_ply

    def fail(*args):
        raise RuntimeError("canonical write intercepted through legacy module")

    monkeypatch.setattr(legacy, "_write_objects", fail)
    with pytest.raises(RuntimeError, match="canonical write intercepted"):
        prepare_scene(write_ply(tmp_path / "input.ply", cloud()), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_both_cli_paths_compile_identical_real_usd_and_pass_independent_audit(tmp_path):
    from simready.evaluation.inspection.physical_assets import inspect_physical_assets
    from tests.fixtures.scene_preparation import cloud, write_ply

    pytest.importorskip("pxr")
    ply = write_ply(tmp_path / "source.ply", cloud())
    spec = {
        "schema_version": 1,
        "source_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "scale_provenance": {
            "status": "verified",
            "method": "synthetic_fixture",
            "evidence": "Known fixture coordinates; not a real-world measurement",
        },
    }
    spec["objects"] = [
        {
            "id": "portable_box",
            "type": "box",
            "bounds_world": {"min": [0, 0, 0.85], "max": [0.1, 0.1, 1.0]},
            "physics": {"motion_type": "dynamic", "mass_kg": 0.5},
        }
    ]
    spec_path = tmp_path / "spec.json"
    write_json(spec_path, spec)
    outputs = [tmp_path / "legacy", tmp_path / "canonical"]
    for module, output in zip(
        (
            "simready.simulation.scene_preparation",
            "simready.assets.compilation.scene_preparation",
        ),
        outputs,
        strict=True,
    ):
        subprocess.run(
            [
                sys.executable,
                "-m",
                module,
                "--ply",
                str(ply),
                "--output",
                str(output),
                "--spec",
                str(spec_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert read_json(output / "preparation.json")["physics_configured"] is True
        assert inspect_physical_assets(output, scene_spec=spec_path)["status"] == "PASS"
    for name in ("appearance.usdc", "objects.usda", "collision.usda", "scene.usda"):
        assert digest(outputs[0] / name) == digest(outputs[1] / name)
