import numpy as np
import pytest

from simready.repair.structural_completion import (
    obstacle_voxels,
    project,
    robust_plane,
    surface_grid,
    voxel_boundary,
)


def test_plane_fit_rejects_foreground_furniture():
    rng = np.random.default_rng(4)
    xy = rng.uniform(-2, 2, (1000, 2))
    z = 0.03 * xy[:, 0] - 0.02 * xy[:, 1] - 0.6 + rng.normal(0, 0.002, 1000)
    z[:350] += rng.uniform(0.2, 1, 350)
    fit, keep = robust_plane(np.c_[xy, z])
    assert np.allclose(fit, [0.03, -0.02, -0.6], atol=0.002)
    assert keep.sum() == 650


def test_surface_image_orientation_and_radial_projection():
    grid, corners = surface_grid(2, [0, 0, 2], [[-1, 1], [-1, 1]], width=3, height=3)
    assert np.allclose(corners, [[-1, -1, 2], [1, -1, 2], [1, 1, 2], [-1, 1, 2]])
    camera = {
        "world_to_camera": np.eye(4).tolist(),
        "K": [[10, 0, 20], [0, 10, 20], [0, 0, 1]],
        "camera_model": "CameraModelId.SIMPLE_RADIAL",
        "camera_params": [10, 20, 20, 0.1],
    }
    uv, z = project(grid, camera)
    assert np.allclose(uv[4], [20, 20])
    assert np.allclose(uv[0], [14.75, 25.25])
    assert (z == 2).all()


def test_adjacent_voxels_have_no_internal_collision_faces():
    occupied = np.ones((2, 1, 1), dtype=bool)
    vertices, faces = voxel_boundary(occupied, np.zeros(3), 0.1)
    assert len(faces) == 20
    assert np.allclose(vertices.min(axis=0), 0)
    assert np.allclose(vertices.max(axis=0), [0.2, 0.1, 0.1])
    triangles = vertices[faces]
    assert not np.any(np.all(np.isclose(triangles[:, :, 0], 0.1), axis=1))


def test_sparse_floaters_do_not_become_obstacles():
    rng = np.random.default_rng(6)
    dense = np.vstack(
        [
            rng.uniform([x, y, z], [x + 0.099, y + 0.099, z + 0.099], (20, 3))
            for x in [0.1, 0.2]
            for y in [0.1, 0.2]
            for z in [0.1, 0.2]
        ]
    )
    points = np.vstack([dense, [0.85, 0.85, 0.85]])
    occupied, _, _ = obstacle_voxels(points, [[0, 0, 0], [1, 1, 1]], minimum_points=5)
    assert occupied.sum() == 8
    assert not occupied[8, 8, 8]
    with pytest.raises(ValueError):
        obstacle_voxels(points, [[0, 0, 0], [100, 100, 100]], resolution=0.03)
