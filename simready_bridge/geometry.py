"""Explicit camera and quaternion conventions shared by reconstruction workers.

Column vectors; OpenCV camera axes are right, down, forward. USD cameras use
right, up, backward. Gaussian files use xyzw; gsplat and PLY use wxyz.
"""

import numpy as np


def look_at_c2w(eye, target, up=(0, 0, 1)):
    eye, target, up = (np.asarray(v, dtype=float) for v in (eye, target, up))
    if any(v.shape != (3,) or not np.isfinite(v).all() for v in (eye, target, up)):
        raise ValueError("Camera vectors must be finite triples")
    forward = target - eye
    if np.linalg.norm(forward) < 1e-10 or np.linalg.norm(up) < 1e-10:
        raise ValueError("Camera direction and up vector must be nonzero")
    forward /= np.linalg.norm(forward)
    up /= np.linalg.norm(up)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-10:
        raise ValueError("Camera direction cannot be parallel to up")
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    matrix = np.eye(4)
    matrix[:3, :3] = np.column_stack((right, down, forward))
    matrix[:3, 3] = eye
    return matrix


def rigid_inverse(matrix):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape[-2:] != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("Finite rigid 4x4 transforms required")
    rotation = matrix[..., :3, :3]
    if (
        not np.allclose(rotation.swapaxes(-1, -2) @ rotation, np.eye(3), atol=1e-6)
        or not np.allclose(np.linalg.det(rotation), 1, atol=1e-6)
        or not np.allclose(matrix[..., 3, :], [0, 0, 0, 1])
    ):
        raise ValueError("Transform is not a rigid pose")
    result = np.broadcast_to(np.eye(4), matrix.shape).copy()
    result[..., :3, :3] = rotation.swapaxes(-1, -2)
    result[..., :3, 3] = -(result[..., :3, :3] @ matrix[..., :3, 3, None])[..., 0]
    return result


def usd_look_at_quaternion(eye, target, up=(0, 0, 1)):
    from scipy.spatial.transform import Rotation

    rotation = look_at_c2w(eye, target, up)[:3, :3] @ np.diag([1, -1, -1])
    xyzw = Rotation.from_matrix(rotation).as_quat()
    return tuple(float(v) for v in xyzw[[3, 0, 1, 2]])


def reorder_quaternion(values, source, target):
    values = np.asarray(values)
    if (
        source not in ("xyzw", "wxyz")
        or target not in ("xyzw", "wxyz")
        or values.shape[-1:] != (4,)
    ):
        raise ValueError("Quaternion order must be explicit: xyzw or wxyz")
    if not np.isfinite(values).all() or (np.linalg.norm(values, axis=-1) == 0).any():
        raise ValueError("Finite nonzero quaternions required")
    return values[..., [source.index(axis) for axis in target]].copy()
