"""The probe may pass only with complete, finite, responsive trajectories."""

import json
import sys
from types import ModuleType

import pytest

from simready.simulation.physical_probe import PROTOCOL, evaluate_response, run_probe


def analytical_rows():
    rows = []
    for step in range(PROTOCOL["steps"] + 1):
        t = step * PROTOCOL["dt_s"]
        for name, mass, y in [("light_mass", 1, 3), ("heavy_mass", 4, 5)]:
            rows.append(
                dict(
                    step=step, t_s=t, body=name, x_m=0.5 / mass * t * t, y_m=y, z_m=1.0
                )
            )
        for name, mu, y in [("low_friction", 0.05, -3), ("high_friction", 0.8, 0)]:
            until_stop = min(t, 2 / (mu * 9.81))
            x = 2 * until_stop - 0.5 * mu * 9.81 * until_stop**2
            rows.append(dict(step=step, t_s=t, body=name, x_m=x, y_m=y, z_m=0.1))
    return rows


def test_analytical_reference_satisfies_declared_tolerances():
    assert evaluate_response(analytical_rows())["status"] == "PASS"


@pytest.mark.parametrize(
    "corruption",
    ["missing", "nan", "stationary", "equal_friction", "wrong_mass", "time"],
)
def test_missing_or_nonphysical_response_cannot_pass(corruption):
    rows = analytical_rows()
    if corruption == "missing":
        rows.pop()
    elif corruption == "nan":
        rows[3]["x_m"] = float("nan")
    elif corruption == "stationary":
        for row in rows:
            row["x_m"] = 0.0
    elif corruption == "equal_friction":
        for row in rows:
            if row["body"] == "high_friction":
                t = row["t_s"]
                row["x_m"] = 2 * t - 0.5 * 0.05 * 9.81 * t**2
    elif corruption == "wrong_mass":
        for row in rows:
            if row["body"] == "heavy_mass":
                row["x_m"] *= 4
    else:
        rows[0]["t_s"] = 1.0
    assert evaluate_response(rows)["status"] == "FAILED"


def test_failed_sdk_startup_records_failure_without_trajectories(tmp_path, monkeypatch):
    sdk = ModuleType("isaacsim")

    def broken_app(*args, **kwargs):
        raise RuntimeError("Synthetic SDK bootstrap failure")

    sdk.SimulationApp = broken_app
    monkeypatch.setitem(sys.modules, "isaacsim", sdk)
    monkeypatch.setattr("importlib.metadata.version", lambda _: "synthetic-test")
    monkeypatch.setenv("EXP_PATH", "/test-only")
    with pytest.raises(RuntimeError, match="bootstrap failure"):
        run_probe(tmp_path / "probe")
    report = json.loads((tmp_path / "probe/probe.json").read_text())
    assert report["status"] == "FAILED"
    assert not (tmp_path / "probe/trajectories.csv").exists()
