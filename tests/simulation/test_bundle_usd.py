"""USD relocation must preserve resolution after both export and scene cloning."""

import shutil

import pytest

from simready.core.artifacts import write_json
from simready.simulation.asset_bundle import (
    BundleExporter,
    copy_layer_with_resolved_paths,
    verify_bundle,
)


def test_relative_layers_resolve_after_relocation(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    source = tmp_path / "source"
    source.mkdir()
    mesh = Usd.Stage.CreateNew(str(source / "mesh.usda"))
    cube = UsdGeom.Cube.Define(mesh, "/Cube")
    mesh.SetDefaultPrim(cube.GetPrim())
    mesh.GetRootLayer().Save()
    stage = Usd.Stage.CreateNew(str(source / "scene.usda"))
    stage.DefinePrim("/Referenced").GetReferences().AddReference("mesh.usda")
    stage.GetRootLayer().Save()
    (source / "video.mp4").write_bytes(b"source fixture")
    write_json(
        source / "spec.json",
        {
            "scene": "starbucks",
            "source_video": "video.mp4",
            "scene_usd": "scene.usda",
            "robot_usd": "mesh.usda",
        },
    )
    destination = tmp_path / "bundle"
    BundleExporter(destination).export(source / "spec.json")
    relocated = tmp_path / "relocated"
    shutil.move(destination, relocated)
    shutil.rmtree(source)
    bundle = verify_bundle(relocated)
    scene = relocated / bundle["scene_usd"]
    loaded = Usd.Stage.Open(str(scene))
    assert loaded.GetPrimAtPath("/Referenced").IsA(UsdGeom.Cube)
    clone = tmp_path / "clone.usda"
    copy_layer_with_resolved_paths(scene, clone)
    loaded_clone = Usd.Stage.Open(str(clone))
    assert loaded_clone.GetPrimAtPath("/Referenced").IsA(UsdGeom.Cube)
