import copy
import json
from pathlib import Path

import pytest

from simready.simulation.button import ButtonSample
from simready.simulation.panel_controls import PanelControl

ROOT = Path(__file__).resolve().parent


def fixture(kind):
    manifest = json.loads((ROOT / "fixtures/controls.json").read_text())
    spec = copy.deepcopy(next(x for x in manifest["buttons"] if x["kind"] == kind))
    writes = []
    control = PanelControl(spec, "/World/Finger", writes.append)
    return spec, control, writes


def tick(control, spec, sequence, depth, force):
    return control.observe(
        ButtonSample(
            sequence,
            (sequence + 1) * 0.002,
            spec["cap_prim"],
            "/World/Finger",
            depth,
            force,
        )
    )


def test_latch_requires_measured_press_and_withdrawal_before_reset():
    spec, c, writes = fixture("estop")
    for i in range(20):
        tick(c, spec, i, 0.0034, 0.0)
    assert not writes
    for i in range(20, 40):
        out = tick(c, spec, i, 0.0034, 1.3)
    assert out["event"] and c.latched and len(writes) == 1
    with pytest.raises(ValueError, match="Withdraw"):
        c.reset_latch()
    tick(c, spec, 40, 0.0034, 0.0)
    assert c.reset_latch() and writes[-1] == 0.0
    assert not c.button.armed
    for i in range(41, 61):
        out = tick(c, spec, i, 0.0, 0.0)
    assert out["rearmed"] and c.button.armed
    assert c.reset_latch() is False


def test_momentary_does_not_write_spring_target_or_allow_reset():
    spec, c, writes = fixture("momentary")
    for i in range(20):
        out = tick(c, spec, i, 0.0034, 1.3)
    assert out["event"] and not c.latched and not writes
    with pytest.raises(ValueError, match="does not implement"):
        c.reset_latch()


def test_bound_contact_pair_must_match():
    spec, c, writes = fixture("estop")
    with pytest.raises(ValueError, match="different bound"):
        c.observe(
            ButtonSample(0, 0.002, spec["cap_prim"], "/World/OtherFinger", 0.0034, 1.3)
        )
    assert not writes
