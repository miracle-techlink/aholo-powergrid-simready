"""Frozen performance gates: baseline calibration precedes candidate execution."""

import math
import statistics

from simready.core.artifacts import object_digest

ERROR_METRICS = (
    "mean_mae",
    "p95_frame_mae",
    "max_frame_mae",
    "p99_tile_mae",
    "max_tile_mae",
    "mean_temporal_error_mae",
)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def calibrate_quality(metrics: dict, policy: dict) -> dict:
    """Apply a predeclared rule to A/A noise; never fit thresholds to a candidate."""
    limits = {}
    unstable = []
    for name in ERROR_METRICS:
        measured, cap = metrics[name], policy["absolute_max"][name]
        if not _finite(measured) or measured < 0 or measured > cap:
            unstable.append(name)
        limits[name] = min(
            cap, measured * policy["noise_multiplier"] + policy["noise_floor"][name]
        )
    if (
        not _finite(metrics["min_ssim"])
        or metrics["min_ssim"] < policy["absolute_min_ssim"]
        or metrics["min_ssim"] > 1
    ):
        unstable.append("min_ssim")
    limits["min_ssim"] = max(
        policy["absolute_min_ssim"],
        1
        - (1 - metrics["min_ssim"]) * policy["noise_multiplier"]
        - policy["ssim_noise_floor"],
    )
    low, high = policy["sharpness_ratio"]
    if (
        not _finite(metrics["sharpness_ratio"])
        or not low <= metrics["sharpness_ratio"] <= high
    ):
        unstable.append("sharpness_ratio")
    limits["sharpness_ratio"] = [low, high]
    return {
        "stable": not unstable,
        "unstable_metrics": unstable,
        "limits": limits,
        "policy_sha256": object_digest(policy),
        "calibration_metrics": metrics,
    }


def check_quality(metrics: dict, calibration: dict) -> dict:
    """Reject local/frame spikes even when average image error is small."""
    checks = {
        name: _finite(metrics.get(name))
        and 0 <= metrics[name] <= calibration["limits"][name]
        for name in ERROR_METRICS
    }
    checks["min_ssim"] = (
        _finite(metrics.get("min_ssim"))
        and calibration["limits"]["min_ssim"] <= metrics["min_ssim"] <= 1
    )
    low, high = calibration["limits"]["sharpness_ratio"]
    checks["sharpness_ratio"] = (
        _finite(metrics.get("sharpness_ratio"))
        and low <= metrics["sharpness_ratio"] <= high
    )
    return {
        "passed": calibration["stable"] and all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }


def compare_runtime(
    baseline_s: list[float], candidate_s: list[float], policy: dict
) -> dict:
    """Require repeated speed and tail checks; do not label two samples p95."""
    if any(not _finite(x) or x <= 0 for x in baseline_s + candidate_s):
        raise ValueError("Invalid runtime sample")
    sufficient = min(len(baseline_s), len(candidate_s)) >= policy["repetitions"]
    if not baseline_s or not candidate_s:
        return {"passed": False, "reason": "missing_runtime_samples"}
    baseline, candidate = statistics.median(baseline_s), statistics.median(candidate_s)
    gain = 1 - candidate / baseline
    checks = {
        "repetitions": sufficient,
        "baseline_stability": max(baseline_s) / min(baseline_s)
        <= policy["max_baseline_ratio"],
        "median_gain": gain >= policy["minimum_gain_fraction"],
        "worst_sample": max(candidate_s)
        <= max(baseline_s) * policy["max_candidate_tail_ratio"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "median_gain_fraction": gain,
        "baseline_median_s": baseline,
        "candidate_median_s": candidate,
        "baseline_samples_s": baseline_s,
        "candidate_samples_s": candidate_s,
    }
