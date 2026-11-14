"""Sealed, inherited proof of an existing ResourcePool device lease."""

import fcntl
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

LEASE_FD = "SIMREADY_GPU_LEASE_FD"
ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008


def lease_uuid(gpu):
    """MIG aliases share the parent's physical lock; missing lineage fails closed."""
    physical = gpu.get("physical_uuid", gpu["uuid"])
    if not re.fullmatch(r"GPU-[A-Za-z0-9-]+", physical):
        raise ValueError(
            "GPU lease requires a physical UUID; MIG inventory must declare physical_uuid"
        )
    return physical


def start_ticks(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def create_capability(lock_fd, lock_path, gpu):
    status = os.fstat(lock_fd)
    ticket = {
        "parent_pid": os.getpid(),
        "parent_start_ticks": start_ticks(os.getpid()),
        "lock_fd": lock_fd,
        "lock": str(Path(lock_path).resolve()),
        "device": status.st_dev,
        "inode": status.st_ino,
        "gpu": gpu,
    }
    descriptor = os.memfd_create("simready-device-lease", os.MFD_ALLOW_SEALING)
    try:
        os.write(descriptor, json.dumps(ticket).encode())
        fcntl.fcntl(descriptor, ADD_SEALS, SEALS)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def inherited_capability():
    """Return a validated ticket, or None; plain environment claims are invalid."""
    if LEASE_FD not in os.environ:
        return None
    try:
        descriptor = int(os.environ[LEASE_FD])
        if fcntl.fcntl(descriptor, GET_SEALS) & SEALS != SEALS:
            raise ValueError("GPU lease capability is not sealed")
        ticket = json.loads(os.pread(descriptor, 16384, 0))
        parent = ticket["parent_pid"]
        if (
            parent != os.getppid()
            or start_ticks(parent) != ticket["parent_start_ticks"]
        ):
            raise ValueError("GPU lease parent identity changed")
        held, named = os.fstat(ticket["lock_fd"]), Path(ticket["lock"]).stat()
        expected = (ticket["device"], ticket["inode"])
        if (
            not stat.S_ISREG(held.st_mode)
            or (held.st_dev, held.st_ino) != expected
            or (named.st_dev, named.st_ino) != expected
            or Path(ticket["lock"]).name != lease_uuid(ticket["gpu"]) + ".lease"
        ):
            raise ValueError("GPU lease descriptor no longer identifies its lock")
        # This descriptor itself must own the flock; a different open descriptor
        # to an externally held inode is not a transferable lease capability.
        fcntl.flock(ticket["lock_fd"], fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Path(ticket["lock"]).open("a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return ticket
            raise ValueError("GPU lease is no longer held")
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Invalid inherited GPU lease capability") from error


@contextmanager
def delegated_capability(values, pass_fds):
    """Relay a verified lease through intermediate managed subprocess wrappers."""
    values = dict(values)
    if values.get("CUDA_VISIBLE_DEVICES") == "":
        values.pop(LEASE_FD, None)
        values.pop("SIMREADY_GPU_UUID", None)
        yield values, tuple(pass_fds)
        return
    if LEASE_FD in values and int(values[LEASE_FD]) in pass_fds:
        # The calling ResourcePool already minted a capability for this child.
        yield values, tuple(pass_fds)
        return
    inherited = inherited_capability()
    if inherited is None:
        if LEASE_FD in values:
            raise RuntimeError("GPU lease environment requires an inherited descriptor")
        yield values, tuple(pass_fds)
        return
    descriptor = create_capability(
        inherited["lock_fd"], inherited["lock"], inherited["gpu"]
    )
    try:
        values.update(
            {
                LEASE_FD: str(descriptor),
                "SIMREADY_GPU_UUID": inherited["gpu"]["uuid"],
                "CUDA_VISIBLE_DEVICES": inherited["gpu"]["uuid"],
            }
        )
        yield values, tuple(set(pass_fds) | {descriptor, inherited["lock_fd"]})
    finally:
        os.close(descriptor)
