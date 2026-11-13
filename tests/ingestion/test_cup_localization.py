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
