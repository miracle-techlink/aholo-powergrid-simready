"""USD relocation must preserve resolution after both export and scene cloning."""

import shutil

import pytest

from simready.core.artifacts import digest, write_json
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


def test_identical_layers_preserve_distinct_source_contexts_after_relocation(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    source = tmp_path / "source"
    for name, geometry in (("one", "Cube"), ("two", "Sphere")):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / "mesh.usda").write_text(
            f'#usda 1.0\ndef {geometry} "Geometry" {{}}\n'
        )
        (directory / "wrapper.usda").write_text(
            "#usda 1.0\n( subLayers = [@mesh.usda@] )\n"
        )
    assert digest(source / "one/wrapper.usda") == digest(source / "two/wrapper.usda")
    (source / "video.mp4").write_bytes(b"source fixture")
    write_json(
        source / "spec.json",
        {
            "scene": "starbucks",
            "source_video": "video.mp4",
            "scene_usd": "one/wrapper.usda",
            "robot_usd": "two/wrapper.usda",
        },
    )
    destination = tmp_path / "bundle"
    exported = BundleExporter(destination).export(source / "spec.json")
    assert exported["scene_usd"] != exported["robot_usd"]
    receipts = {row["path"]: row for row in exported["files"]}
    first, second = receipts[exported["scene_usd"]], receipts[exported["robot_usd"]]
    assert first["source_sha256"] == second["source_sha256"]
    assert first["sha256"] != second["sha256"]
    for path in destination.rglob("*"):
        if path.suffix in (".json", ".usda"):
            assert str(source) not in path.read_text()
    relocated = tmp_path / "relocated"
    shutil.move(destination, relocated)
    shutil.rmtree(source)
    verified = verify_bundle(relocated)
    first_stage = Usd.Stage.Open(str(relocated / verified["scene_usd"]))
    second_stage = Usd.Stage.Open(str(relocated / verified["robot_usd"]))
    assert first_stage.GetPrimAtPath("/Geometry").IsA(UsdGeom.Cube)
    assert second_stage.GetPrimAtPath("/Geometry").IsA(UsdGeom.Sphere)


def mdl_fixture(source, asset_name, module=None):
    from pxr import Sdf, Usd

    source.mkdir()
    if module is not None:
        (source / asset_name).write_bytes(module)
    stage = Usd.Stage.CreateNew(str(source / "scene.usda"))
    stage.DefinePrim("/Material").CreateAttribute(
        "info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset
    ).Set(Sdf.AssetPath(asset_name))
    stage.GetRootLayer().Save()
    (source / "video.mp4").write_bytes(b"source fixture")
    write_json(
        source / "spec.json",
        {
            "scene": "starbucks",
            "source_video": "video.mp4",
            "scene_usd": "scene.usda",
            "robot_usd": "scene.usda",
        },
    )
    return source / "spec.json"


@pytest.mark.parametrize("name", ["local.mdl", "OmniPBR.mdl"])
def test_local_mdl_is_copied_before_sdk_exemption_and_survives_relocation(
    tmp_path, name
):
    pytest.importorskip("pxr")
    from pxr import Usd

    source = tmp_path / "source"
    module = b'mdl 1.0;\n// No import ::other or texture_2d("missing.png")\nexport material plain() = material();\n'
    spec = mdl_fixture(source, name, module)
    destination = tmp_path / "bundle"
    exported = BundleExporter(destination).export(spec)
    copied = next(row for row in exported["files"] if row["path"].endswith(name))
    assert (destination / copied["path"]).read_bytes() == module
    relocated = tmp_path / "relocated"
    shutil.move(destination, relocated)
    shutil.rmtree(source)
    verified = verify_bundle(relocated)
    stage = Usd.Stage.Open(str(relocated / verified["scene_usd"]))
    asset = stage.GetPrimAtPath("/Material").GetAttribute("info:mdl:sourceAsset").Get()
    assert asset.resolvedPath == str(relocated / copied["path"])


def test_only_explicit_sdk_mdl_may_remain_external(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd

    source = tmp_path / "source"
    spec = mdl_fixture(source, "OmniPBR.mdl")
    destination = tmp_path / "bundle"
    bundle = BundleExporter(destination).export(spec)
    verify_bundle(destination)
    stage = Usd.Stage.Open(str(destination / bundle["scene_usd"]))
    assert (
        stage.GetPrimAtPath("/Material").GetAttribute("info:mdl:sourceAsset").Get().path
        == "OmniPBR.mdl"
    )


@pytest.mark.parametrize("name", ["missing.mdl", "./OmniPBR.mdl", "OmniPBR.MDL"])
def test_unknown_or_explicit_local_missing_mdl_fails_without_success_manifest(
    tmp_path, name
):
    pytest.importorskip("pxr")
    source = tmp_path / "source"
    spec = mdl_fixture(source, name)
    destination = tmp_path / "bundle"
    with pytest.raises(FileNotFoundError):
        BundleExporter(destination).export(spec)
    assert not (destination / "bundle.json").exists()


@pytest.mark.parametrize(
    "module",
    [
        b"mdl 1.0; import ::helper::*;",
        b"mdl 1.0; using . import helper;",
        b"mdl 1.0; export material m() = ::helper::m();",
        b'mdl 1.0; texture_2d("../textures/albedo.png");',
        b'mdl 1.0; texture_3d("../textures/volume.exr");',
        b'mdl 1.0; texture_cube("../textures/cube.png");',
        b'mdl 1.0; texture_ptex("../textures/mesh.ptx");',
        b'mdl 1.0; light_profile("../profiles/light.ies");',
        b'mdl 1.0; bsdf_measurement("../measured/material.mbsdf");',
        b"\xff\xfeinvalid-utf8",
    ],
)
def test_custom_mdl_dependencies_are_explicitly_unsupported(tmp_path, module):
    pytest.importorskip("pxr")
    source = tmp_path / "source"
    spec = mdl_fixture(source, "local.mdl", module)
    destination = tmp_path / "bundle"
    with pytest.raises(ValueError, match="self-contained UTF-8"):
        BundleExporter(destination).export(spec)
    assert not (destination / "bundle.json").exists()
