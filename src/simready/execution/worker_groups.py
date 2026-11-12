"""Bounded workers sharing one owned GPU lease, never another job's lease."""

import fcntl
import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from simready.core.artifacts import write_json
from simready.execution.profiling import profile_command, summarize_profiles

# Linux UAPI values from linux/fcntl.h. The deployed uv CPython 3.12 build
# exposes fcntl() but omits these constants; the kernel still supports sealing.
_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
_REQUIRED_SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008


def _start_ticks(pid):
    # comm may contain spaces and parentheses; fields after the last ')' are stable.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


@contextmanager
def gpu_worker_lease():
    """Child-only entry; requires a sealed capability inherited from its parent."""
    fd = int(os.environ["SIMREADY_GPU_LEASE_FD"])
    if fcntl.fcntl(fd, _GET_SEALS) & _REQUIRED_SEALS != _REQUIRED_SEALS:
        raise RuntimeError("GPU lease capability is not sealed")
    ticket = json.loads(os.pread(fd, 8192, 0))
    parent = ticket["parent_pid"]
    if parent != os.getppid() or _start_ticks(parent) != ticket["parent_start_ticks"]:
        raise RuntimeError("GPU lease parent identity changed")
    lock = Path(ticket["lock"])
    if lock.read_text() != ticket["identity"]:
        raise RuntimeError("GPU lease ownership changed")
    if any(p != lock for p in lock.parent.glob("*.gpu.lock")):
        raise RuntimeError("Another legacy GPU lock is present")
    yield lock


def run_worker_group(jobs, *, directory, max_workers=3, lock_dir="/tmp"):
    """Run independent profiled commands under one bounded scheduler lease.

    This coordinates participating jobs only. Existing noncooperating desktop or
    streaming applications are neither stopped nor claimed to be isolated.
    Child workers must use gpu_worker_lease, and must not spawn GPU grandchildren.
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
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "simready_runtime.gpu.lock"
    if list(root.glob("*.gpu.lock")):
        raise RuntimeError("SIMULATION_RESOURCE_BUSY; existing locks are retained")
    identity = f"{os.getpid()} simready_group {secrets.token_hex(16)}\n"
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    capability = None
    records = []
    started = time.monotonic()
    report = {
        "status": "RUNNING",
        "max_workers": max_workers,
        "started_unix_s": time.time(),
    }
    try:
        os.write(lock_fd, identity.encode())
        os.close(lock_fd)
        lock_fd = None
        if any(p != lock for p in root.glob("*.gpu.lock")):
            raise RuntimeError("Another legacy GPU lock appeared")
        capability = os.memfd_create("simready-worker-group", os.MFD_ALLOW_SEALING)
        os.write(
            capability,
            json.dumps(
                {
                    "parent_pid": os.getpid(),
                    "parent_start_ticks": _start_ticks(os.getpid()),
                    "lock": str(lock),
                    "identity": identity,
                }
            ).encode(),
        )
        fcntl.fcntl(
            capability,
            _ADD_SEALS,
            _REQUIRED_SEALS,
        )
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
                    "SIMREADY_GPU_LEASE_FD": str(capability),
                },
                token_mode=job.get("token_mode", "unknown"),
                pass_fds=(capability,),
                metadata={"shared_group_lease": True, "max_workers": max_workers},
            )

        # Joining the executor precedes lease release, including on exceptions.
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            records = list(pool.map(run, jobs))
        report["status"] = (
            "SUCCESS" if all(r["status"] == "SUCCESS" for r in records) else "FAILED"
        )
    except BaseException:
        report["status"] = "ERROR"
        raise
    finally:
        if capability is not None:
            os.close(capability)
        if lock_fd is not None:
            os.close(lock_fd)
        if lock.is_file() and lock.read_text() == identity:
            lock.unlink()
        report.update(
            ended_unix_s=time.time(),
            wall_seconds=time.monotonic() - started,
            summary=summarize_profiles(records),
        )
        write_json(directory / "group.json", report)
    return report
