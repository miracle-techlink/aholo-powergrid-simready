"""Analytic sensor/registration checks with no simulator oracle dependency."""

import numpy as np
import pytest

from simready.ingestion.cup_localization import backproject, fit_translation


def test_pinhole_rays_include_camera_extrinsics():
    depth = np.ones((3, 3)) * 2
    transform = np.eye(4)
    transform[:3, 3] = [3, 4, 5]
    points = backproject(
        depth,
        {"intrinsics": [[2, 0, 1], [0, 2, 1], [0, 0, 1]], "camera_to_world": transform},
    )
    np.testing.assert_allclose(points[1, 1], [3, 4, 7])
    np.testing.assert_allclose(points[2, 0], [2, 5, 7])


def test_partial_surface_registration_recovers_translation():
    angle, z = np.meshgrid(np.linspace(0, 2 * np.pi, 300), np.linspace(0, 0.1, 40))
    radius = 0.03 + 0.1 * z
    model = np.c_[
        radius.ravel() * np.cos(angle.ravel()),
        radius.ravel() * np.sin(angle.ravel()),
        z.ravel(),
    ]
    shift = np.array([0.5, -0.3, 0.85])
    observed = model[model[:, 0] > 0.005] + shift
    estimate, error = fit_translation(observed, model, 0.85)
    np.testing.assert_allclose(estimate, shift, atol=0.001)
    assert error < 0.002


def test_wrong_size_model_rejected():
    rng = np.random.default_rng(2)
    with pytest.raises(ValueError, match="Model fit rejected"):
        fit_translation(
            rng.normal(size=(500, 3)) * 0.2 + [0, 0, 0.85],
            rng.normal(size=(500, 3)) * 0.01,
            0.85,
        )


def test_invalid_depth_remains_invalid():
    result = backproject(
        np.array([[np.inf, -1.0]]),
        {"intrinsics": np.eye(3), "camera_to_world": np.eye(4)},
    )
    assert np.isnan(result).all()


@pytest.mark.parametrize(
    "error",
    ["scale", "reflection", "nonfinite", "bottom_row", "skew", "intrinsics_bottom"],
)
def test_invalid_camera_cannot_silently_change_object_geometry(error):
    k, pose = np.eye(3), np.eye(4)
    if error == "scale":
        pose[0, 0] = 2
    elif error == "reflection":
        pose[0, 0] = -1
    elif error == "nonfinite":
        pose[0, 3] = np.nan
    elif error == "bottom_row":
        pose[3, 0] = 1
    elif error == "skew":
        k[0, 1] = 0.1
    else:
        k[2, 2] = 2
    with pytest.raises(ValueError):
        backproject(np.ones((2, 2)), {"intrinsics": k, "camera_to_world": pose})


def test_localization_and_scene_projection_agree_on_valid_camera():
    from simready.simulation.depth_projection import backproject_depth

    depth = np.array([[1.0, np.nan], [2.0, 0.0]])
    pose = np.eye(4)
    pose[:3, 3] = [1, 2, 3]
    k = np.array([[4, 0, 0.5], [0, 3, 0.5], [0, 0, 1]])
    dense = backproject(depth, {"intrinsics": k, "camera_to_world": pose})
    sparse, pixels, _, _, _ = backproject_depth(depth, pose, k)
    np.testing.assert_array_equal(dense.reshape(-1, 3)[pixels], sparse)
