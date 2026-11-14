"""Planar geometry shared by repair and physical observation workers."""

import numpy as np


def inside_convex_polygon(point, polygon):
    """Boundary-inclusive 2D test, independent of winding direction."""
    p = np.asarray(polygon, float)
    edges = np.roll(p, -1, axis=0) - p
    delta = np.asarray(point, float) - p
    cross = edges[:, 0] * delta[:, 1] - edges[:, 1] * delta[:, 0]
    return bool((cross >= -1e-8).all() or (cross <= 1e-8).all())
