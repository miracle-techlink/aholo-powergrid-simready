"""Restart an owned worker after a transport exit or crash, with one deadline."""

import math
import os
import time
from pathlib import Path

from simready.core.artifacts import write_json
from simready.execution.processes.local import ProcessTimeoutError, run_process


def supervise_process(
    argv,
    *,
    cwd,
    directory,
    timeout,
    max_attempts=3,
    retry_delay=2,
    env=None,
):
    """Exit 75 or a signal is retryable; other nonzero codes fail closed.

    argv is unchanged across attempts. Workers must resume a persisted receipt
    and respect SIMREADY_JOB_DEADLINE_UNIX_S. This supervisor also enforces the
    deadline externally and retains each attempt's stdout/stderr separately.
    """
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or not math.isfinite(retry_delay)
        or retry_delay < 0
    ):
        raise ValueError("Invalid supervision budget")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("Positive attempt count required")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + timeout
    child_env = {**os.environ, **(env or {})}
    child_env["SIMREADY_JOB_DEADLINE_UNIX_S"] = str(time.time() + timeout)
    report = {"status": "RUNNING", "attempts": [], "started_unix_s": time.time()}
    write_json(directory / "supervision.json", report)
    try:
        for index in range(max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["status"] = "TIMEOUT"
                break
            attempt = directory / f"attempt_{index}"
            try:
                result = run_process(
                    argv,
                    cwd=cwd,
                    timeout=remaining,
                    stdout=attempt / "stdout.log",
                    stderr=attempt / "stderr.log",
                    env=child_env,
                )
            except ProcessTimeoutError as error:
                report["attempts"].append(
                    {"index": index, "status": "TIMEOUT", "seconds": error.seconds}
                )
                report["status"] = "TIMEOUT"
                break
            report["attempts"].append(
                {
                    "index": index,
                    "returncode": result.returncode,
                    "seconds": result.seconds,
                }
            )
            if result.returncode == 0:
                report["status"] = "SUCCESS"
                break
            report["status"] = "FAILED"
            if result.returncode != 75 and result.returncode >= 0:
                break
            write_json(directory / "supervision.json", report)
            if index + 1 < max_attempts:
                time.sleep(min(retry_delay, max(0, deadline - time.monotonic())))
    except BaseException:
        report["status"] = "ERROR"
        raise
    finally:
        report.update(ended_unix_s=time.time(), wall_seconds=time.monotonic() - started)
        write_json(directory / "supervision.json", report)
    return report
