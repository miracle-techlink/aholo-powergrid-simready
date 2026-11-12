"""Runtime gains must not hide local damage or unstable controls."""

from copy import deepcopy

import pytest

from simready.evaluation.performance_gate import (
    calibrate_quality,
    check_quality,
    compare_runtime,
)


def policy():
    names = [
        "mean_mae",
        "p95_frame_mae",
        "max_frame_mae",
        "p99_tile_mae",
        "max_tile_mae",
        "mean_temporal_error_mae",
    ]
    return {
        "absolute_max": dict.fromkeys(names, 5.0),
        "noise_multiplier": 1.5,
        "noise_floor": dict.fromkeys(names, 0.1),
        "absolute_min_ssim": 0.97,
        "ssim_noise_floor": 0.002,
        "sharpness_ratio": [0.97, 1.03],
    }


def metrics():
    return {
        **dict.fromkeys(policy()["absolute_max"], 0.2),
        "min_ssim": 0.995,
        "sharpness_ratio": 1.0,
    }


def test_local_damage_rejected_despite_good_global_average():
    calibration = calibrate_quality(metrics(), policy())
    candidate = metrics()
    candidate["max_tile_mae"] = 4.0
    result = check_quality(candidate, calibration)
    assert result["failed_checks"] == ["max_tile_mae"]
    assert not result["passed"]


def test_candidate_never_changes_frozen_calibration():
    calibration = calibrate_quality(metrics(), policy())
    frozen = deepcopy(calibration)
    candidate = metrics()
    candidate["mean_mae"] = 100
    assert not check_quality(candidate, calibration)["passed"]
    assert calibration == frozen


def test_unstable_control_cannot_authorize_candidate():
    noisy = metrics()
    noisy["max_frame_mae"] = 6
    calibration = calibrate_quality(noisy, policy())
    assert not calibration["stable"]
    assert not check_quality(metrics(), calibration)["passed"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_nonfinite_or_negative_metric_fails(bad):
    candidate = metrics()
    candidate["mean_mae"] = bad
    assert not check_quality(candidate, calibrate_quality(metrics(), policy()))[
        "passed"
    ]


def test_speed_requires_repetitions_stable_controls_and_gain():
    p = {
        "repetitions": 2,
        "minimum_gain_fraction": 0.08,
        "max_baseline_ratio": 1.1,
        "max_candidate_tail_ratio": 1.1,
    }
    assert compare_runtime([100.0, 102.0], [80.0, 82.0], p)["passed"]
    assert not compare_runtime([100.0, 102.0], [80.0], p)["passed"]
    assert not compare_runtime([100.0, 130.0], [80.0, 82.0], p)["passed"]
    assert not compare_runtime([100.0, 102.0], [99.0, 99.0], p)["passed"]


def test_impossible_ssim_cannot_authorize_a_runtime():
    impossible = metrics()
    impossible["min_ssim"] = 1.1
    assert not calibrate_quality(impossible, policy())["stable"]
    assert not check_quality(impossible, calibrate_quality(metrics(), policy()))[
        "passed"
    ]
