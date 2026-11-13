import numpy as np

from examples.starbucks_simready.check_route import inspect_route


def test_route_includes_voxel_volume_and_clearance_not_only_centers():
    profile = {str(i): [[-0.1, -0.1], [0.1, 0.1]] for i in range(4)}
    rows = [{"frame": 0, "robot_position": [0, 0, 0], "yaw": 0}]
    # The first cell center is outside the body, but its occupied volume plus
    # the required clearance intersects it. The second cell is safely clear.
    centers = np.array([[0.14, 0, 0.05], [0.22, 0, 0.05], [0, 0, 2.0]])
    result = inspect_route(rows, centers, 0.10, np.zeros(3), profile)
    assert result[0]["overlapping_obstacle_cells"] == 1


def test_route_rotates_asymmetric_robot_shape():
    profile = {str(i): [[-0.3, -0.1], [0.3, 0.1]] for i in range(4)}
    centers = np.array([[0.25, 0, 0.05]])
    rows = [
        {"frame": i, "robot_position": [0, 0, 0], "yaw": yaw}
        for i, yaw in enumerate([0, 90, 180])
    ]
    result = inspect_route(rows, centers, 0.05, np.zeros(3), profile)
    assert [x["overlapping_obstacle_cells"] for x in result] == [1, 0, 1]
