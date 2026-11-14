"""Bounded monitoring of an existing remote job; never submits a new job."""

import json
import math
import time
from pathlib import Path

from simready.core.artifacts import write_json


class RetryablePollError(Exception):
    """A transport failure for which retrying the same read is safe."""


class RemoteJobError(RuntimeError):
    """The remote job reached a terminal failure state."""


def poll_existing_job(
    fetch,
    *,
    job_id: str,
    directory: Path,
    timeout: float,
    interval: float = 10,
    max_backoff: float = 30,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """Fetch receives the remaining budget; transport must enforce it too.

    Persist only state and error types, never server payloads, URLs or credentials.
    The event log survives restarts; heartbeat.json is the latest observation.
    """
    if not job_id or any(
        not math.isfinite(v) or v <= 0 for v in (timeout, interval, max_backoff)
    ):
        raise ValueError("Job ID and finite positive polling budgets required")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    deadline = clock() + timeout
    failures = 0
    success = {"SUCCEEDED", "SUCCESS", "DONE"}
    failed = {"FAILED", "FAIL", "CANCELED", "CANCELLED", "TIMEOUT", "REJECTED"}
    pending = {"PENDING", "PREPROCESSING", "RUNNING", "QUEUED"}
    while (remaining := deadline - clock()) > 0:
        event = {"job_id": job_id, "observed_unix_s": time.time()}
        try:
            job = fetch(remaining)
            status = str(job.get("status", "")).upper()
            if status not in success | failed | pending:
                raise ValueError("Unknown remote job state")
            event.update(status=status, consecutive_transport_failures=0)
            failures = 0
            delay = interval
        except RetryablePollError:
            failures += 1
            event.update(
                status="TRANSPORT_RETRY", consecutive_transport_failures=failures
            )
            delay = min(max_backoff, interval * 2 ** min(failures - 1, 16))
            status = "TRANSPORT_RETRY"
        with (directory / "poll_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
            stream.flush()
        write_json(directory / "heartbeat.json", event)
        if clock() > deadline:
            break
        if status in failed:
            raise RemoteJobError("Remote job ended with " + status)
        if status in success:
            return job
        sleep(min(delay, max(0, deadline - clock())))
    raise TimeoutError("Polling budget exhausted; resume the existing job receipt")
