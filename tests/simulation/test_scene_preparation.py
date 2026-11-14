"""Numerical encoding, bounded geometry and real USD composition contracts."""

import shutil

import numpy as np
import pytest

from simready.simulation.gaussian_asset import (
    decode_vertices,
    read_ply,
    validate_sim3,
)
from simready.simulation.scene_preparation import (
    fit_floor_plane,
    prepare_scene,
    voxel_proxies,
)
from tests.fixtures.scene_preparation import cloud, write_ply


def verified_spec():
    return {
        "schema_version": 1,
        "source_to_world": np.eye(4).tolist(),
        "scale_provenance": {
            "status": "verified",
            "method": "fixture dimensions",
            "evidence": "synthetic test geometry",
        },
        "floor_plane": {"normal": [0, 0, 1], "offset": 0, "evidence": "fixture z=0"},
    }


def test_reference_3dgs_sh_channel_order_and_activations(tmp_path):
    data = decode_vertices(
        read_ply(write_ply(tmp_path / "source.ply", [[0, 0, 0], [1, 2, 3]]))
    )
    assert data["kind"] == "gaussian_splats"
    # PLY log-scales and exp output are float32; allow their rounding on all CPUs.
    np.testing.assert_allclose(
        data["scales"], 0.02, rtol=2 * np.finfo(np.float32).eps, atol=0
    )
    np.testing.assert_allclose(data["rotations"], [[1, 0, 0, 0]] * 2)
    np.testing.assert_allclose(data["opacities"], 0.5)
    np.testing.assert_allclose(
        data["sh"][:4], [[0, 0.1, 0.2], [0, 3, 6], [1, 4, 7], [2, 5, 8]]
    )


def test_rgb_points_have_no_invented_gaussian_parameters(tmp_path):
    data = decode_vertices(
        read_ply(write_ply(tmp_path / "rgb.ply", [[0, 0, 0]], gaussian=False))
    )
    assert data["kind"] == "rgb_points"
    assert "scales" not in data
    np.testing.assert_allclose(data["colors"], [[1, 128 / 255, 0]])


@pytest.mark.parametrize(
    "property_name,value",
    [
        ("x", np.nan),
        ("rot_0", 0),
        ("scale_0", 1000),
        ("scale_0", -1000),
        ("opacity", np.inf),
    ],
)
def test_bad_numeric_encoding_rejected(tmp_path, property_name, value):
    properties = read_ply(write_ply(tmp_path / "bad.ply", [[0, 0, 0]]))
    properties[property_name][0] = value
    with pytest.raises(ValueError):
        decode_vertices(properties)


def test_missing_sh_coefficient_rejected(tmp_path):
    properties = read_ply(write_ply(tmp_path / "bad.ply", [[0, 0, 0]]))
    del properties["f_rest_4"]
    with pytest.raises(ValueError, match="SH"):
        decode_vertices(properties)


def test_resource_limit_rejected_before_reading(tmp_path):
    path = tmp_path / "huge.ply"
    path.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 999999999\nproperty float x\nend_header\n"
    )
    with pytest.raises(ValueError, match="resource limit"):
        read_ply(path)


def test_truncated_body_rejected(tmp_path):
    path = write_ply(tmp_path / "truncated.ply", [[0, 0, 0]])
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(ValueError, match="Truncated"):
        read_ply(path)


@pytest.mark.parametrize(
    "matrix",
    [
        np.diag([1, 2, 1, 1]),
        np.diag([-1, 1, 1, 1]),
        np.diag([0, 0, 0, 1]),
        np.full((4, 4), np.nan),
    ],
)
def test_non_similarity_rejected(matrix):
    with pytest.raises(ValueError):
        validate_sim3(matrix)


def test_floor_ransac_finds_tilted_ground_and_rejects_wall():
    points = cloud()
    points[:, 2] += 0.06 * points[:, 0] + 0.1
    floor = fit_floor_plane(points, threshold=0.01)
    assert floor["source"] == "low_point_ransac"
    normal = np.array([-0.06, 0, 1])
    normal /= np.linalg.norm(normal)
    np.testing.assert_allclose(floor["normal"], normal, atol=1e-6)
    assert floor["rmse"] < 1e-6
    wall = np.random.default_rng(1).uniform([0, -2, 0], [0, 2, 2], (500, 3))
    assert fit_floor_plane(wall) is None


def test_voxel_proxy_cap_and_negative_world_coordinates():
    points = np.random.default_rng(3).uniform(-3, 3, (1000, 3))
    proxies, size = voxel_proxies(points, 0.05, 20, 1)
    assert 0 < len(proxies) <= 20
    assert size > 0.05
    for point in points:
        assert any(
            np.all(point >= box["min"]) and np.all(point <= box["max"])
            for box in proxies
        )


def test_usd_layers_relocate_and_preserve_sh_frame(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom, UsdVol

    path = write_ply(tmp_path / "input.ply", cloud())
    transform = np.array(
        [[0, -2, 0, 1], [2, 0, 0, 2], [0, 0, 2, 3], [0, 0, 0, 1]], dtype=float
    )
    output = tmp_path / "prepared"
    report = prepare_scene(
        path,
        output,
        {
            "source_to_world": transform.tolist(),
            "collision": {"enabled": False},
            "camera": {},
            "render": {},
        },
    )
    assert report["preview_ready"] and not report["metric_scale_verified"]
    moved = tmp_path / "moved"
    shutil.move(output, moved)
    path.unlink()
    stage = Usd.Stage.Open(str(moved / report["scene"]))
    prim = stage.GetPrimAtPath("/World/Appearance/Source/Splats")
    assert prim.IsA(UsdVol.ParticleField3DGaussianSplat)
    matrix = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
    np.testing.assert_allclose(matrix.Transform(Gf.Vec3d(1, 2, 3)), [-3, 4, 9])
    # Obtain through the schema to avoid depending on attribute namespace spelling.
    sh = UsdVol.ParticleField3DGaussianSplat(
        prim
    ).GetRadianceSphericalHarmonicsCoefficientsAttr()
    assert sh.GetMetadata("elementSize") == 4
    np.testing.assert_allclose(
        np.asarray(sh.Get())[:4], [[0, 0.1, 0.2], [0, 3, 6], [1, 4, 7], [2, 5, 8]]
    )


def test_explicit_box_and_button_have_visible_geometry_resolved_joint_and_honest_status(
    tmp_path,
):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    spec = verified_spec()
    spec["objects"] = [
        {
            "id": "Cabinet",
            "type": "box",
            "bounds_world": {"min": [-1, 0, 0], "max": [-0.8, 0.5, 1]},
            "color": [0.4, 0.5, 0.6],
            "evidence": "operator dimensions",
        },
        {
            "id": "Button",
            "type": "button",
            "position_world": [0, 0, 1],
            "axis": "Y",
            "evidence": "operator placement",
        },
    ]
    output = tmp_path / "prepared"
    report = prepare_scene(write_ply(tmp_path / "input.ply", cloud()), output, spec)
    assert report["physics_ready"] and report["declared_scale_verified"]
    assert (
        report["provenance_is_user_supplied"]
        and not report["independent_scale_validation"]
    )
    assert not report["task_validated"]
    stage = Usd.Stage.Open(str(output / "scene.usda"))
    assert (
        UsdGeom.Imageable(
            stage.GetPrimAtPath("/World/Objects/Cabinet/Shape")
        ).ComputeVisibility()
        == "inherited"
    )
    assert (
        UsdGeom.Imageable(
            stage.GetPrimAtPath("/World/Collision/Floor")
        ).ComputeVisibility()
        == "invisible"
    )
    joint = UsdPhysics.PrismaticJoint(
        stage.GetPrimAtPath("/World/Objects/Button/Joint")
    )
    assert joint.GetAxisAttr().Get() == "Y"
    assert joint.GetLowerLimitAttr().Get() == pytest.approx(-0.004)
    assert joint.GetUpperLimitAttr().Get() == 0
    assert list(joint.GetBody0Rel().GetTargets()) == ["/World/Objects/Button/Base"]
    assert list(joint.GetBody1Rel().GetTargets()) == ["/World/Objects/Button/Cap"]
    assert stage.GetPrimAtPath("/World/Objects/Button/Cap/Shape").HasAPI(
        UsdPhysics.CollisionAPI
    )
    assert UsdPhysics.DriveAPI(joint, "linear").GetStiffnessAttr().Get() == 400
    material = UsdPhysics.MaterialAPI(
        stage.GetPrimAtPath("/World/Objects/ContactPreset")
    )
    assert material.GetStaticFrictionAttr().Get() == pytest.approx(0.8)
    assert report["collision"]["contact_material"]["dynamic_friction"] == 0.7


def test_unknown_frame_does_not_invent_floor_or_verified_scale(tmp_path):
    pytest.importorskip("pxr")
    report = prepare_scene(
        write_ply(tmp_path / "input.ply", cloud()), tmp_path / "prepared"
    )
    assert not report["world_up_declared"]
    assert report["floor"] is None
    assert not report["physics_ready"]
    assert not report["metric_scale_verified"]


def test_rgb_cloud_is_visible_usd_points_and_has_estimated_floor(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    output = tmp_path / "prepared"
    report = prepare_scene(
        write_ply(tmp_path / "input.ply", cloud(), gaussian=False),
        output,
        {"world_up_axis": "Z"},
    )
    assert report["floor"]["source"] == "low_point_ransac"
    assert not report["metric_scale_verified"]
    stage = Usd.Stage.Open(str(output / "scene.usda"))
    points = UsdGeom.Points(stage.GetPrimAtPath("/World/Appearance/Source/Points"))
    assert len(points.GetPointsAttr().Get()) == len(cloud())
    assert points.GetWidthsInterpolation() == "constant"


def test_failed_write_leaves_no_partial_success_directory(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from simready.simulation import scene_preparation

    def fail(*args):
        raise RuntimeError("simulated USD write failure")

    monkeypatch.setattr(scene_preparation, "_write_objects", fail)
    output = tmp_path / "prepared"
    with pytest.raises(RuntimeError, match="simulated USD write failure"):
        prepare_scene(write_ply(tmp_path / "input.ply", cloud()), output)
    assert not output.exists()
    assert not list(tmp_path.glob(".scene-preparation-*"))


@pytest.mark.parametrize(
    "spec",
    [
        {
            "scale_provenance": {
                "status": "verified",
                "method": "guess",
                "evidence": "none",
            }
        },
        {
            "source_to_world": np.eye(4).tolist(),
            "scale_provenance": {"status": "verified"},
        },
        {"collision": {"max_proxies": 100000}},
        {"collision": {"max_points": True}},
        {"objects": [{"id": "../bad", "type": "box"}]},
        {"floor_plane": {"normal": [1, 0, 0], "offset": 0}},
        {"world_up_axis": "Y"},
        {"objectz": []},
    ],
)
def test_invalid_spec_leaves_no_output(tmp_path, spec):
    pytest.importorskip("pxr")
    source = write_ply(tmp_path / "input.ply", cloud())
    output = tmp_path / "prepared"
    with pytest.raises(ValueError):
        prepare_scene(source, output, spec)
    assert not output.exists()


def test_existing_output_preserved(tmp_path):
    pytest.importorskip("pxr")
    output = tmp_path / "prepared"
    output.mkdir()
    marker = output / "preparation.json"
    marker.write_text("preserve successful run")
    with pytest.raises(FileExistsError):
        prepare_scene(write_ply(tmp_path / "input.ply", cloud()), output)
    assert marker.read_text() == "preserve successful run"
