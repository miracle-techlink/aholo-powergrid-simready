"""Reject ambiguous lamp/button scenes and check calibrated depth geometry."""

import cv2
import numpy as np

from simready.simulation.panel_perception import detect_stop_candidate, localize_rgbd


def panel_image(two=False):
    image = np.full((200, 600 if two else 300, 3), 190, np.uint8)
    for x in [0, 300] if two else [0]:
        for u, color in [(90, (0, 180, 0)), (150, (255, 160, 0)), (210, (220, 0, 0))]:
            cv2.circle(image, (x + u, 55), 9, color, -1)
        cv2.circle(image, (x + 150, 120), 20, (245, 170, 0), -1)
        cv2.circle(image, (x + 150, 120), 12, (230, 0, 0), -1)
        cv2.circle(image, (x + 90, 120), 10, (230, 0, 0), -1)
        cv2.circle(image, (x + 210, 120), 10, (0, 180, 0), -1)
    return image


def test_unique_panel_and_lamps_are_not_buttons():
    image = panel_image()
    d = detect_stop_candidate(image)
    assert d["found"] and np.allclose(d["pixel"], [90, 120])
    image[90:150] = 190
    assert not detect_stop_candidate(image)["found"]


def test_ambiguous_identical_panels_are_rejected():
    d = detect_stop_candidate(panel_image(two=True))
    assert not d["found"] and len(d["candidates"]) == 2


def test_depth_requires_valid_measurement_and_camera_transform():
    d = detect_stop_candidate(panel_image())
    depth = np.full((200, 300), 2.0, np.float32)
    pose = np.eye(4)
    pose[:3, 3] = [1, 2, 3]
    p = localize_rgbd(d, depth, pose, 200)
    assert np.allclose(p["surface_world_m"], [0.4, 1.8, 1.0])
    depth[:] = np.inf
    try:
        localize_rgbd(d, depth, pose, 200)
    except ValueError:
        pass
    else:
        raise AssertionError("Missing depth must not produce a target")
