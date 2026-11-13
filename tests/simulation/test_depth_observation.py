import copy

import numpy as np
import pytest

from simready.geometry.cameras import look_at_c2w
from simready.simulation.depth_observation import (
    calibration_to_usd_camera,
    normalize_camera_z,
)


def _camera():
    return {
        "schema_version": 1,
        "camera_convention": "opencv_c2w",
        "distortion": "none",
        "image_size": [640, 360],
        "intrinsics": [[400, 0, 319.5], [0, 400, 179.5], [0, 0, 1]],
        "camera_to_world": look_at_c2w([2, 3, 4], [0, 0, 0]).tolist(),
    }


def test_camera_axes_convert_without_changing_optical_center():
    calibration = _camera()
    output = calibration_to_usd_camera(calibration)
    usd_column = np.array(output["usd_transform_row_major"]).T
    cv = np.array(calibration["camera_to_world"])
    assert np.allclose(usd_column[:, 3], cv[:, 3])
    assert np.allclose(usd_column[:3, 0], cv[:3, 0])
    assert np.allclose(usd_column[:3, 1], -cv[:3, 1])
    assert np.allclose(usd_column[:3, 2], -cv[:3, 2])
    assert output["horizontal_aperture_offset_mm"] == 0
    assert output["vertical_aperture_offset_mm"] == 0


@pytest.mark.parametrize("cx,cy", [(319.5, 179.5), (270.0, 205.0)])
def test_usd_frustum_projection_matches_opencv_including_lens_shift(cx, cy):
    gf = pytest.importorskip("pxr.Gf")
    calibration = _camera()
    calibration["intrinsics"][0][2] = cx
    calibration["intrinsics"][1][2] = cy
    settings = calibration_to_usd_camera(calibration)
    camera = gf.Camera()
    camera.focalLength = settings["focal_length_mm"]
    camera.horizontalAperture = settings["horizontal_aperture_mm"]
    camera.verticalAperture = settings["vertical_aperture_mm"]
    camera.horizontalApertureOffset = settings["horizontal_aperture_offset_mm"]
    camera.verticalApertureOffset = settings["vertical_aperture_offset_mm"]
    projection = np.array(camera.frustum.ComputeProjectionMatrix()).T
    cv_points = np.array([[0, 0, 2], [0.4, -0.2, 2], [-0.3, 0.4, 3]])
    usd_points = cv_points * [1, -1, -1]
    clips = np.c_[usd_points, np.ones(len(usd_points))] @ projection.T
    ndc = clips[:, :2] / clips[:, 3:4]
    raster = np.c_[(ndc[:, 0] + 1) * 320 - 0.5, (1 - ndc[:, 1]) * 180 - 0.5]
    expected = cv_points[:, :2] / cv_points[:, 2:] * 400 + [cx, cy]
    assert np.allclose(raster, expected, atol=2e-5, rtol=0)


@pytest.mark.parametrize(
    "mutation",
    [
        "reflection",
        "scale",
        "skew",
        "nonsquare",
        "distortion",
        "principal",
        "bad_size",
        "nan",
        "schema",
    ],
)
def test_invalid_calibration_is_rejected(mutation):
    calibration = copy.deepcopy(_camera())
    if mutation == "reflection":
        calibration["camera_to_world"] = np.diag([-1, 1, 1, 1]).tolist()
    elif mutation == "scale":
        calibration["camera_to_world"] = np.diag([2, 2, 2, 1]).tolist()
    elif mutation == "skew":
        calibration["intrinsics"][0][1] = 2
    elif mutation == "nonsquare":
        calibration["intrinsics"][1][1] = 390
    elif mutation == "distortion":
        calibration["distortion"] = "fisheye"
    elif mutation == "principal":
        calibration["intrinsics"][0][2] = 900
    elif mutation == "bad_size":
        calibration["image_size"] = [True, 360]
    elif mutation == "nan":
        calibration["intrinsics"][0][0] = float("nan")
    else:
        calibration["schema_version"] = True
    with pytest.raises(ValueError):
        calibration_to_usd_camera(calibration)


def test_depth_keeps_camera_z_without_converting_to_euclidean_range():
    raw = np.array([[2, 2, 2], [np.nan, np.inf, 0]], dtype=np.float32)
    clean, stats = normalize_camera_z(raw, width=3, height=2, meters_per_unit=1.0)
    assert clean.dtype == np.float32
    assert np.array_equal(clean, [[2, 2, 2], [0, 0, 0]])
    assert stats["valid_fraction"] == 0.5
    assert stats["kind"] == "camera_z"
    assert stats["unit"] == "world_units"
    assert stats["metric_scale_verified"] is False
    assert np.isnan(raw[1, 0])


@pytest.mark.parametrize(
    "raw",
    [
        np.zeros((2, 3)),
        np.ones((3, 2)),
        np.array([[1, -1, 1], [1, 1, 1]]),
        np.full((2, 3), np.inf),
    ],
)
def test_bad_depth_fails(raw):
    with pytest.raises(ValueError):
        normalize_camera_z(raw, width=3, height=2, meters_per_unit=1.0)


def test_unknown_stage_unit_conversion_is_not_silently_applied():
    with pytest.raises(ValueError, match="metersPerUnit"):
        normalize_camera_z(np.ones((2, 3)), width=3, height=2, meters_per_unit=0.01)


def test_single_depth_channel_is_accepted():
    clean, _ = normalize_camera_z(
        np.ones((2, 3, 1)), width=3, height=2, meters_per_unit=1.0
    )
    assert clean.shape == (2, 3)
