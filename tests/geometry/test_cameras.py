import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from simready.geometry.cameras import (
    look_at_c2w,
    reorder_quaternion,
    rigid_inverse,
    usd_look_at_quaternion,
)


@pytest.mark.parametrize(
    "eye,target",
    [([0, 0, 0], [1, 0, 0]), ([3, -2, 4], [1, 5, 1]), ([0, 0, 0], [-1, 0, 0])],
)
def test_camera_center_projection_and_usd_orientation(eye, target):
    c2w = look_at_c2w(eye, target)
    w2c = rigid_inverse(c2w)
    np.testing.assert_allclose(w2c @ np.r_[eye, 1], [0, 0, 0, 1], atol=1e-12)
    projected = w2c @ np.r_[target, 1]
    np.testing.assert_allclose(projected[:2], 0, atol=1e-12)
    assert projected[2] > 0
    np.testing.assert_allclose(c2w @ w2c, np.eye(4), atol=1e-12)
    quaternion = reorder_quaternion(usd_look_at_quaternion(eye, target), "wxyz", "xyzw")
    forward = Rotation.from_quat(quaternion).apply([0, 0, -1])
    wanted = np.subtract(target, eye)
    wanted = wanted / np.linalg.norm(wanted)
    np.testing.assert_allclose(forward, wanted, atol=1e-12)


def test_camera_rejects_degenerate_vectors():
    for target in ([0, 0, 0], [0, 0, 1], [float("nan"), 1, 1]):
        with pytest.raises(ValueError):
            look_at_c2w([0, 0, 0], target)
    with pytest.raises(ValueError):
        rigid_inverse(np.zeros((4, 4)))
