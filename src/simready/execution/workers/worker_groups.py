"""Bounded workers sharing one owned GPU lease, never another job's lease."""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from simready.core.artifacts import write_json
from simready.execution.resources.gpu_capability import inherited_capability
from simready.execution.resources.resource_pool import DEFAULT_LOCK_DIR
from simready.execution.workers.device_lease import device_lease
from simready.execution.workers.profiling import profile_command, summarize_profiles


@contextmanager
def gpu_worker_lease():
    """Child-only entry; requires a sealed capability inherited from its parent."""
    ticket = inherited_capability()
    if ticket is None:
        raise RuntimeError("A verified inherited GPU lease is required")
    if Path(ticket["lock"]).read_text():
        raise RuntimeError("GPU lease is quarantined")
    yield Path(ticket["lock"])


def run_worker_group(
    jobs,
    *,
    directory,
    max_workers=3,
    lock_dir=DEFAULT_LOCK_DIR,
    gpu="0",
    device_inventory=None,
):
    """Run independent profiled commands under one bounded scheduler lease.

    This coordinates participating jobs only. Existing noncooperating desktop or
    streaming applications are neither stopped nor claimed to be isolated.
    Child workers use gpu_worker_lease. Nested managed launchers may delegate the
    same sealed lease; independently acquired GPU devices are not permitted.
    """
    if type(max_workers) is not int or not 1 <= max_workers <= 3:
        raise ValueError("Worker group capacity must be between one and three")
    if not hasattr(os, "memfd_create"):
        raise RuntimeError("The group launcher requires Linux os.memfd_create support")
    if not jobs or len({job["name"] for job in jobs}) != len(jobs):
        raise ValueError("Distinct nonempty worker jobs required")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", job["name"]) for job in jobs):
        raise ValueError("Worker names must be plain directory identifiers")
    directory, root = Path(directory).resolve(), Path(lock_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    lease_context = device_lease(
        resource_kind="cuda", gpu=gpu, device_inventory=device_inventory, lock_dir=root
    )
    lease = None
    records = []
    futures = []
    cancellation = Event()
    started = time.monotonic()
    report = {
        "status": "RUNNING",
        "max_workers": max_workers,
        "started_unix_s": time.time(),
    }
    try:
        lease = lease_context.__enter__()
        report["gpu_uuid"] = lease.gpu["uuid"]
        write_json(directory / "group.json", report)

        def run(job):
            return profile_command(
                job["argv"],
                name=job["name"],
                directory=directory / job["name"],
                cwd=job["cwd"],
                timeout_s=job["timeout_s"],
                env={
                    **os.environ,
                    **job.get("env", {}),
                    **lease.environment,
                },
                token_mode=job.get("token_mode", "unknown"),
                pass_fds=lease.pass_fds,
                metadata={"shared_group_lease": True, "max_workers": max_workers},
                cancel_event=cancellation,
            )

        # Joining the executor precedes lease release, including on exceptions.
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            try:
                for job in jobs:
                    futures.append((job, pool.submit(run, job)))
                records = [future.result() for _, future in futures]
            except BaseException:
                cancellation.set()
                raise
        report["status"] = (
            "SUCCESS"
            if all(r["status"] == "SUCCESS" for r in records) and not lease.quarantined
            else "FAILED"
        )
        report["device_quarantined"] = lease.quarantined
    except BaseException as error:
        report["status"] = "ERROR" if isinstance(error, Exception) else "INTERRUPTED"
        raise
    finally:
        # Preserve completed/cancelled profiles even when one future raised.
        records = []
        for job, future in futures:
            try:
                records.append(future.result())
            except BaseException as error:
                records.append(
                    {
                        "stage": job["name"],
                        "status": "ERROR"
                        if isinstance(error, Exception)
                        else "INTERRUPTED",
                        "usage": None,
                        "error": type(error).__name__,
                    }
                )
        if lease is not None:
            lease_context.__exit__(None, None, None)
        report.update(
            ended_unix_s=time.time(),
            wall_seconds=time.monotonic() - started,
            summary=summarize_profiles(records),
        )
        write_json(directory / "group.json", report)
    return report
