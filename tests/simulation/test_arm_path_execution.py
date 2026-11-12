import numpy as np

from simready.simulation.arm_planner import advance_joint_target


def test_command_stays_on_checked_joint_segment_with_unequal_deltas():
    start = np.array([1.8, 1.5, -1.0, -0.5, -0.3, 0.0])
    end = np.array([0.0, 0.5, -0.9, 0.0, 0.3, 0.0])
    q = start.copy()
    delta = end - start
    active = abs(delta) > 1e-8
    for _ in range(2000):
        new = advance_joint_target(q, end, 0.002, 0.5)
        assert np.max(abs(new - q)) <= 0.001000001
        fractions = (new - start)[active] / delta[active]
        assert np.ptp(fractions) < 1e-9
        q = new
    assert np.allclose(q, end)


def test_zero_length_segment_does_not_move():
    q = np.zeros(6)
    assert np.array_equal(advance_joint_target(q, q, 0.002), q)
