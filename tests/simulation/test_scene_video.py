"""Camera plans must be finite and reproducible before an Isaac process starts."""

import numpy as np
import pytest

from simready.simulation.scene_video import camera_samples

BOUNDS = {"min": [-2, -3, 0], "max": [2, 3, 4]}


def test_survey_follows_explicit_camera_without_old_scene_coordinates():
    camera = {"eye": [1, 0, 1], "target": [0, 0, 1], "orbit_degrees": 90}
    rows = camera_samples(BOUNDS, camera, 3)
    assert rows[0]["eye"] == camera["eye"]
    assert np.allclose(rows[-1]["eye"], [0, 1, 1])
    assert all(row["target"] == camera["target"] for row in rows)


@pytest.mark.parametrize(
    "camera",
    [
        {"eye": [1, 0, 1]},
        {"eye": [0, 0, 2], "target": [0, 0, 1]},
        {"orbit_degrees": float("nan")},
        {"eye": [1, 0, float("inf")], "target": [0, 0, 0]},
        {"unknown": 1},
    ],
)
def test_ambiguous_or_degenerate_camera_is_rejected(camera):
    with pytest.raises(ValueError):
        camera_samples(BOUNDS, camera, 4)


def test_bounds_derived_camera_scales_with_the_actual_scene():
    original = camera_samples(BOUNDS, {}, 2)
    scaled = {key: (np.asarray(value) * 3).tolist() for key, value in BOUNDS.items()}
    actual = camera_samples(scaled, {}, 2)
    assert np.allclose(actual[0]["eye"], np.asarray(original[0]["eye"]) * 3)
