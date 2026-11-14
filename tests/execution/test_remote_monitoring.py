"""Fault injection for transport recovery and owned-process supervision."""

import json
import sys

import pytest

from simready.core.artifacts import read_json
from simready.execution.processes.remote_polling import (
    RemoteJobError,
    RetryablePollError,
    poll_existing_job,
)
from simready.execution.processes.supervision import supervise_process


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_transport_failures_resume_same_read_and_preserve_history(tmp_path):
    clock = Clock()
    calls = []
    states = iter([RetryablePollError(), RetryablePollError(), "RUNNING", "SUCCEEDED"])

    def fetch(remaining):
        calls.append(remaining)
        value = next(states)
        if isinstance(value, Exception):
            raise value
        return {"status": value, "signed_url": "secret-must-not-be-logged"}

    assert (
        poll_existing_job(
            fetch,
            job_id="same-world",
            directory=tmp_path,
            timeout=60,
            interval=2,
            clock=clock,
            sleep=clock.sleep,
        )["status"]
        == "SUCCEEDED"
    )
    assert calls == [60, 58, 54, 52]
    # A process restart appends rather than overwriting the recovery evidence.
    poll_existing_job(
        lambda remaining: {"status": "SUCCEEDED"},
        job_id="same-world",
        directory=tmp_path,
        timeout=60,
        clock=clock,
        sleep=clock.sleep,
    )
    text = (tmp_path / "poll_events.jsonl").read_text()
    assert "secret" not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert len(events) == 5
    assert {event["job_id"] for event in events} == {"same-world"}
    assert read_json(tmp_path / "heartbeat.json")["status"] == "SUCCEEDED"


@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "TIMEOUT", "REJECTED"])
def test_terminal_failure_never_retried(tmp_path, status):
    calls = []

    def fetch(remaining):
        calls.append(remaining)
        return {"status": status}

    with pytest.raises(RemoteJobError, match=status):
        poll_existing_job(fetch, job_id="x", directory=tmp_path, timeout=60)
    assert len(calls) == 1


def test_transport_backoff_stops_at_global_deadline(tmp_path):
    clock = Clock()

    def fetch(remaining):
        raise RetryablePollError()

    with pytest.raises(TimeoutError):
        poll_existing_job(
            fetch,
            job_id="x",
            directory=tmp_path,
            timeout=7,
            interval=2,
            clock=clock,
            sleep=clock.sleep,
        )
    assert clock.now == 7


def test_supervisor_recovers_sigkill_without_resubmission(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import os,signal,pathlib\n"
        "receipt=pathlib.Path('receipt')\n"
        "if not receipt.exists():\n"
        "    receipt.write_text('world-original')\n"
        "    os.kill(os.getpid(),signal.SIGKILL)\n"
        "assert receipt.read_text()=='world-original'\n"
        "assert 'SIMREADY_JOB_DEADLINE_UNIX_S' in os.environ\n"
        "print('resumed same receipt',flush=True)\n"
    )
    report = supervise_process(
        [sys.executable, str(script)],
        cwd=tmp_path,
        directory=tmp_path / "supervisor",
        timeout=10,
        retry_delay=0,
    )
    assert report["status"] == "SUCCESS"
    assert [a["returncode"] for a in report["attempts"]] == [-9, 0]
    assert "resumed" in (tmp_path / "supervisor/attempt_1/stdout.log").read_text()


def test_supervisor_does_not_retry_permanent_failure_or_reset_timeout(tmp_path):
    failed = supervise_process(
        [sys.executable, "-c", "raise SystemExit(2)"],
        cwd=tmp_path,
        directory=tmp_path / "failed",
        timeout=10,
        retry_delay=0,
    )
    assert failed["status"] == "FAILED" and len(failed["attempts"]) == 1
    timed = supervise_process(
        [sys.executable, "-c", "import time;time.sleep(30)"],
        cwd=tmp_path,
        directory=tmp_path / "timed",
        timeout=0.15,
        retry_delay=0,
    )
    assert timed["status"] == "TIMEOUT" and len(timed["attempts"]) == 1
    assert timed["wall_seconds"] < 5
