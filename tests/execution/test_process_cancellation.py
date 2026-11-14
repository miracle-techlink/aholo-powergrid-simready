"""Cancellation boundaries exercise owned CPU processes without GPU or cloud."""

import sys
from threading import Event

import pytest

from simready.execution.processes import ProcessCancelledError, run_process


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1, True, None])
def test_invalid_timeout_cannot_start_process_or_create_logs(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        run_process(
            [sys.executable, "-c", "raise AssertionError('must not run')"],
            cwd=tmp_path,
            timeout=timeout,
            stdout=tmp_path / "logs/stdout",
            stderr=tmp_path / "logs/stderr",
        )
    assert not (tmp_path / "logs").exists()


def test_already_cancelled_process_is_never_started(tmp_path):
    event = Event()
    event.set()
    with pytest.raises(ProcessCancelledError):
        run_process(
            [sys.executable, "-c", "raise AssertionError('must not run')"],
            cwd=tmp_path,
            timeout=10,
            stdout=tmp_path / "stdout",
            stderr=tmp_path / "stderr",
            cancel_event=event,
        )
    assert not list(tmp_path.iterdir())


def test_cancellable_communication_preserves_large_stdin(tmp_path):
    payload = "test input\n" * 20000
    result = run_process(
        [
            sys.executable,
            "-c",
            "import sys,time; time.sleep(.25); sys.stdout.write(sys.stdin.read())",
        ],
        cwd=tmp_path,
        timeout=5,
        stdout=tmp_path / "stdout",
        stderr=tmp_path / "stderr",
        input_text=payload,
        cancel_event=Event(),
    )
    assert result.returncode == 0
    assert (tmp_path / "stdout").read_text() == payload
