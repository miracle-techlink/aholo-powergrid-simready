"""Contact validity, missing-sample and component/mission contract regressions."""

from dataclasses import replace

import pytest

from simready.simulation.button import (
    ButtonParameters,
    ButtonSample,
    ContactButton,
    apply_usd_feedback,
)
from simready.simulation.task_state import MissionConfig

PARAMS = ButtonParameters(0.002, 0.04, 0.025, 0.006, 1.0, 0.3)
DEFINITION = {
    "states": ["off", "on"],
    "initial": "off",
    "transitions": {"off->on": {"action": "press"}, "on->off": {"action": "press"}},
    "visual_feedback": {
        "off": {"color": [1.0, 0.0, 0.0], "emission": 0.0},
        "on": {"color": [0.0, 1.0, 0.0], "emission": 0.5},
    },
}


def runtime():
    return ContactButton("source-hash#button", DEFINITION, PARAMS, "/Button", "/Tool")


def sample(i, depth=0.033, force=30.0):
    return ButtonSample(i, (i + 1) * 0.002, "/Button", "/Tool", depth, force)


def test_force_and_travel_are_both_required():
    r = runtime()
    for i in range(50):
        assert r.observe(sample(i, force=0.0))["event"] is None
    for i in range(50, 100):
        assert r.observe(sample(i, depth=0.0))["event"] is None
    assert r.state == "off"


def test_hold_requires_observed_release_before_second_press():
    r = runtime()
    events = [r.observe(sample(i))["event"] for i in range(100)]
    assert sum(e is not None for e in events) == 1
    # A time gap is not evidence of physical release.
    assert r.observe(sample(500))["event"] is None
    for i in range(501, 521):
        r.observe(sample(i, depth=0.00245, force=0.0))
    events = [r.observe(sample(i))["event"] for i in range(521, 541)]
    assert sum(e is not None for e in events) == 1
    assert r.state == "off"


def test_gap_breaks_press_debounce():
    r = runtime()
    for i in range(19):
        assert r.observe(sample(i))["event"] is None
    assert r.observe(sample(100))["event"] is None
    assert r.press_count == 1


def test_wrong_pair_duplicate_and_invalid_measurements_rejected():
    r = runtime()
    r.observe(sample(0))
    with pytest.raises(ValueError):
        r.observe(sample(0))
    for s in [
        replace(sample(1), counterpart_prim="/Cabinet"),
        replace(sample(1), depth_m=0.05),
        replace(sample(1), contact_force_N=-1.0),
        replace(sample(1), contact_force_N=float("nan")),
    ]:
        with pytest.raises(ValueError):
            r.observe(s)
    assert r.state == "off"


def test_fixture_mission_travel_mismatch_is_rejected():
    with pytest.raises(ValueError, match="maximum travel"):
        PARAMS.check_mission_config(MissionConfig())
    c = PARAMS.mission_config()
    assert c.max_button_travel_m == 0.04
    assert c.press_threshold_m == 0.025


def test_feedback_cannot_write_another_button():
    pytest.importorskip("pxr")
    from pxr import Usd

    stage = Usd.Stage.CreateInMemory()
    result = runtime().observe(sample(0, depth=0.0, force=0.0))
    with pytest.raises(ValueError, match="binding"):
        apply_usd_feedback(stage, "/AnotherButton", "/AnotherButton/Cap", result)
    with pytest.raises(ValueError, match="binding"):
        apply_usd_feedback(stage, "/Button", "/AnotherButton/Cap", result)
