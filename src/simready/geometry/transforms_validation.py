"""Finite arrays and positive Sim3 validation without asset or simulator dependencies."""

import numpy as np


def finite_array(value, name, shape=None):
    result = np.asarray(value, dtype=np.float64)
    if (shape is not None and result.shape != shape) or not np.isfinite(result).all():
        raise ValueError(f"Invalid finite {name}, expected shape {shape}")
    return result


def validate_sim3(value):
    """Return a positive, orientation-preserving column-vector similarity."""
    transform = finite_array(value, "source_to_world", (4, 4))
    if not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-9, rtol=0):
        raise ValueError("source_to_world must be affine")
    linear = transform[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0]))
    if (
        not 1e-8 <= scale <= 1e8
        or not np.allclose(
            linear.T @ linear, np.eye(3) * scale**2, rtol=1e-6, atol=1e-12 * scale**2
        )
        or np.linalg.det(linear) <= 0
    ):
        raise ValueError("source_to_world must be positive uniform scale and rotation")
    return transform
