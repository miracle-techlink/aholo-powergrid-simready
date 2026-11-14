"""CPU semantic stages are owned processes and leave terminal evidence on stop."""

import sys
import time
from threading import Event, Timer

import pytest

from examples.assets.semantic_fixture import create_fixture
from simready.core.artifacts import read_json
from simready.execution.processes.local import (
    ProcessCancelledError,
    ProcessTimeoutError,
)
from simready.workflows.pipelines import semantic_assets


@pytest.mark.parametrize("stage", ["projection", "authoring"])
@pytest.mark.parametrize("termination", ["timeout", "cancel"])
def test_heavy_cpu_stage_stop_kills_descendants_and_keeps_evidence(
    tmp_path, monkeypatch, stage, termination
):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")
    output = tmp_path / "workflow"
    marker = tmp_path / "escaped-child"
    cancellation = Event()
    real_worker = semantic_assets._worker
    stages = []

    def slow_stage(command, log, timeout, **kwargs):
        if stage not in command:
            return real_worker(command, log, timeout, **kwargs)
        stages.append(stage)
        assert "internal-worker" in command
        assert 0 < timeout <= 5
        child = (
            "import pathlib,time; time.sleep(.7); "
            f"pathlib.Path({str(marker)!r}).touch()"
        )
        program = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
            "print('started',flush=True); time.sleep(20)"
        )
        timer = Timer(0.2, cancellation.set) if termination == "cancel" else None
        if timer:
            timer.start()
        try:
            return real_worker(
                [sys.executable, "-c", program, child],
                log,
                min(timeout, 0.3) if termination == "timeout" else timeout,
                **kwargs,
            )
        finally:
            if timer:
                timer.cancel()

    monkeypatch.setattr(semantic_assets, "_worker", slow_stage)
    expected = (
        ProcessTimeoutError if termination == "timeout" else ProcessCancelledError
    )
    with pytest.raises(expected):
        semantic_assets.run_semantic_assets(
            **inputs, output_dir=output, timeout_s=5, cancel_event=cancellation
        )
    assert stages == [stage]
    report = read_json(output / "workflow.json")
    failed_stage = "projection_and_priors" if stage == "projection" else "usd_authoring"
    status = "TIMEOUT" if termination == "timeout" else "CANCELLED"
    assert report["status"] == status and report["failed_stage"] == failed_stage
    assert report["steps"][-1]["wall_s"] > 0
    assert report["steps"][-1]["status"] == status
    assert "started" in (output / "logs" / f"{failed_stage}.log").read_text()
    assert not (output / "physical_audit.json").exists()
    time.sleep(0.75)
    assert not marker.exists()


def test_total_budget_is_not_reset_after_projection(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")
    real_worker = semantic_assets._worker
    budgets = []

    def record(command, log, timeout, **kwargs):
        budgets.append(timeout)
        return real_worker(command, log, timeout, **kwargs)

    monkeypatch.setattr(semantic_assets, "_worker", record)
    report = semantic_assets.run_semantic_assets(
        **inputs, output_dir=tmp_path / "workflow", timeout_s=30
    )
    assert report["physical_audit_status"] == "PASS"
    assert len(budgets) == 3 and 0 < budgets[2] < budgets[1] < budgets[0] <= 30
