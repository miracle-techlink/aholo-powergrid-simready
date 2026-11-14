"""Projection evidence, occlusion and refusal to invent physical identity."""

import numpy as np
import pytest
from PIL import Image

from examples.assets.semantic_fixture import create_fixture
from simready.core.artifacts import digest, read_json, write_json
from simready.simulation.depth_projection import backproject_depth
from simready.simulation.semantic_projection import (
    build_semantic_spec,
    visible_point_pixels,
)
from simready.workflows.pipelines.semantic_assets import run_semantic_assets


def test_front_surface_occlusion_and_pixel_convention():
    intrinsics = np.array([[10, 0, 5], [0, 10, 5], [0, 0, 1]])
    points = np.array([[0, 0, 2], [0, 0, 5], [0.4, 0, 2], [0, 0, -1], [100, 0, 2]])
    indices, x, y = visible_point_pixels(points, np.eye(4), intrinsics, (11, 11))
    np.testing.assert_array_equal(indices, [0, 2])
    np.testing.assert_array_equal(x, [5, 7])
    np.testing.assert_array_equal(y, [5, 5])


def test_depth_backprojection_preserves_source_pixels_and_rejects_background():
    depth = np.array([[2.0, 2.0, np.inf], [0.0, np.nan, 4.0]], dtype=np.float32)
    pose = np.eye(4)
    pose[:3, 3] = [10, 20, 30]
    points, ids, x, y, count = backproject_depth(depth, pose, np.eye(3))
    assert count == 3
    np.testing.assert_array_equal(ids, [0, 1, 5])
    np.testing.assert_allclose(points, [[10, 20, 32], [12, 20, 32], [18, 24, 34]])
    np.testing.assert_array_equal(x, [0, 1, 2])
    np.testing.assert_array_equal(y, [0, 0, 1])


def test_registered_depth_changes_index_space_and_keeps_four_asset_candidates(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    manifest = read_json(inputs["segmentation_manifest"])
    depth = np.zeros((60, 80), dtype=np.float32)
    depths = {"cabinet": 5.0, "table": 4.0, "chair": 3.5, "floor": 4.0}
    for row in manifest["instances"]:
        mask = (
            np.array(
                Image.open(inputs["segmentation_manifest"].parent / row["mask"]["path"])
            )
            > 0
        )
        depth[mask] = depths[row["category"]]
    path = inputs["calibration"].parent / "depth.npy"
    np.save(path, depth)
    camera = read_json(inputs["calibration"])
    camera["depth"] = {
        "path": path.name,
        "sha256": digest(path),
        "kind": "camera_z",
        "unit": "world_units",
    }
    write_json(inputs["calibration"], camera)
    result = build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert result["status"] == "CANDIDATE_READY"
    assert len(result["accepted"]) == 4
    assert result["observations"]["source_index_domain_size"] == 80 * 60
    assert result["observations"]["image_size"] == [80, 60]
    assert (
        result["observations"]["point_assignment_index_space"]
        == "depth_raster_linear_index"
    )
    assignments = np.load(tmp_path / "projection" / "point_assignments.npz")
    for row in result["accepted"]:
        values = assignments[row["object_id"]]
        assert (depth.ravel()[values] == depths[row["category"]]).all()


def test_reject_camera_scale_and_skew():
    with pytest.raises(ValueError, match="rigid"):
        visible_point_pixels(
            np.ones((2, 3)), np.diag([2, 2, 2, 1]), np.eye(3), (10, 10)
        )
    intrinsics = np.eye(3)
    intrinsics[0, 1] = 0.1
    with pytest.raises(ValueError, match="unskewed"):
        visible_point_pixels(np.ones((2, 3)), np.eye(4), intrinsics, (10, 10))


def test_four_categories_have_different_contact_hypotheses(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    result = build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert result["status"] == "CANDIDATE_READY"
    assert {row["category"] for row in result["accepted"]} == {
        "cabinet",
        "table",
        "chair",
        "floor",
    }
    spec = read_json(tmp_path / "projection" / "scene_spec.json")
    assert len(spec["objects"]) == 4
    assert (
        len({obj["physics"]["contact"]["static_friction"] for obj in spec["objects"]})
        == 3
    )
    for obj in spec["objects"]:
        assert obj["physics"]["motion_type"] == "static"
        assert "mass_kg" not in obj["physics"]
        assert obj["visible"] is False
    assert result["semantic_accuracy"] == "NOT_EVALUATED"
    assert not result["release_authorized"]


@pytest.mark.parametrize(
    "key", ["image_sha256", "ply_sha256", "source_to_world_sha256"]
)
def test_wrong_source_binding_rejected_before_geometry(tmp_path, key):
    inputs = create_fixture(tmp_path / "inputs")
    calibration = read_json(inputs["calibration"])
    calibration[key] = "0" * 64
    write_json(inputs["calibration"], calibration)
    with pytest.raises(ValueError, match="hash mismatch"):
        build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert not (tmp_path / "projection").exists()


def test_overlapping_masks_rejected_even_with_updated_hashes(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    manifest = read_json(inputs["segmentation_manifest"])
    mask = (
        inputs["segmentation_manifest"].parent
        / manifest["instances"][1]["mask"]["path"]
    )
    values = np.asarray(Image.open(mask)).copy()
    values[10, 10] = 255  # pixel already owned by cabinet
    Image.fromarray(values).save(mask)
    manifest["instances"][1]["mask"]["sha256"] = digest(mask)
    manifest["instances"][1]["area_px"] += 1
    write_json(inputs["segmentation_manifest"], manifest)
    with pytest.raises(ValueError, match="Overlapping"):
        build_semantic_spec(**inputs, output_dir=tmp_path / "projection")


def test_no_3d_support_is_review_required(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    result = build_semantic_spec(
        **inputs, output_dir=tmp_path / "projection", min_points=999_999
    )
    assert result["status"] == "REVIEW_REQUIRED"
    assert "no_supported_3d_asset" in result["review_reasons"]
    assert len(result["skipped"]) == 4


def test_dynamic_automatic_prior_is_rejected(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    priors = tmp_path / "priors.json"
    write_json(priors, {"cabinet": {"motion_type": "dynamic", "mass_kg": 42}})
    with pytest.raises(ValueError, match="metric scale"):
        build_semantic_spec(
            **inputs, output_dir=tmp_path / "projection", priors_file=priors
        )


def test_semantic_proxy_cannot_obstruct_declared_button(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    first = build_semantic_spec(**inputs, output_dir=tmp_path / "first")
    bounds = next(
        row["bounds_world"] for row in first["accepted"] if row["category"] == "cabinet"
    )
    center = (np.array(bounds["min"]) + bounds["max"]) / 2
    spec = read_json(inputs["scene_spec"])
    spec["objects"] = [
        {"id": "existing_control", "type": "button", "position_world": center.tolist()}
    ]
    write_json(inputs["scene_spec"], spec)
    second = build_semantic_spec(**inputs, output_dir=tmp_path / "second")
    assert second["status"] == "REVIEW_REQUIRED"
    assert any(
        row.get("conflicting_object") == "existing_control" for row in second["skipped"]
    )
    final = read_json(tmp_path / "second" / "scene_spec.json")
    assert final["objects"][0] == spec["objects"][0]
    assert not any(row["category"] == "cabinet" for row in second["accepted"])


def test_pipeline_real_usd_and_fresh_interpreter_audit(tmp_path):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")
    result = run_semantic_assets(**inputs, output_dir=tmp_path / "result")
    assert result["status"] == "CANDIDATE_READY"
    assert result["physical_audit_status"] == "PASS"
    assert result["assets_generated"] == 4
    assert result["steps"][0]["mode"] == "reuse_manifest"
    assert not result["release_authorized"]
    assert result["wall_s"] >= sum(row["wall_s"] for row in result["steps"])
    assert (
        len(read_json(tmp_path / "result" / "scene" / "asset_catalog.json")["objects"])
        == 4
    )


def test_background_floor_with_semantic_floor_is_blocked(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    spec = read_json(inputs["scene_spec"])
    spec["collision"]["enabled"] = True
    write_json(inputs["scene_spec"], spec)
    result = build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert result["status"] == "REVIEW_REQUIRED"
    assert "background_floor_may_overlap_semantic_floor" in result["review_reasons"]


def test_model_requested_review_is_not_cleared_by_usd_readback(tmp_path):
    inputs = create_fixture(tmp_path / "inputs")
    manifest = read_json(inputs["segmentation_manifest"])
    manifest["disambiguation"] = {"semantic_review_required": True}
    write_json(inputs["segmentation_manifest"], manifest)
    result = build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert result["status"] == "REVIEW_REQUIRED"
    assert "semantic_review_requested_by_model" in result["review_reasons"]


def test_calibration_changed_after_read_cannot_receive_a_valid_receipt(
    tmp_path, monkeypatch
):
    import simready.simulation.semantic_projection as projection

    inputs = create_fixture(tmp_path / "inputs")
    decode = projection.decode_vertices

    def change_camera(properties):
        value = read_json(inputs["calibration"])
        value["camera_to_world"][0][3] += 99
        write_json(inputs["calibration"], value)
        return decode(properties)

    monkeypatch.setattr(projection, "decode_vertices", change_camera)
    with pytest.raises(ValueError, match="evidence changed"):
        build_semantic_spec(**inputs, output_dir=tmp_path / "projection")
    assert not (tmp_path / "projection").exists()
