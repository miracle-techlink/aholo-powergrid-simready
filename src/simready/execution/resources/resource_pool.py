"""Cooperative, exclusive device leases shared by processes on one Linux host."""

import fcntl
import glob
import json
import os
import threading
from pathlib import Path

from simready.execution.resources.gpu_capability import (
    LEASE_FD,
    create_capability,
    inherited_capability,
    lease_uuid,
)

DEFAULT_LOCK_DIR = "/tmp/simready-device-leases"


class Lease:
    def __init__(self, pool, resources, gpu=None, handle=None):
        self.pool, self.resources, self.gpu, self.handle = (
            pool,
            dict(resources),
            gpu,
            handle,
        )
        self.closed = False
        self.capability = (
            create_capability(
                handle.fileno(), pool.directory / f"{lease_uuid(gpu)}.lease", gpu
            )
            if handle
            else None
        )

    @property
    def pass_fds(self):
        # Child retains the flock if the scheduler exits unexpectedly. We close
        # our descriptor without LOCK_UN, which would unlock the child's copy.
        return (self.handle.fileno(), self.capability) if self.handle else ()

    @property
    def environment(self):
        if self.gpu is None:
            return {"CUDA_VISIBLE_DEVICES": ""}
        return {
            "CUDA_VISIBLE_DEVICES": self.gpu["uuid"],
            "SIMREADY_GPU_UUID": self.gpu["uuid"],
            LEASE_FD: str(self.capability),
        }

    def close(self):
        with self.pool.guard:
            if self.closed:
                return
            self.closed = True
            self.pool.cpu += self.resources.get("cpu", 1)
            self.pool.ram += self.resources["ram_gb"]
            if self.resources["kind"] == "cloud":
                self.pool.cloud += 1
            if self.handle:
                os.close(self.capability)
                self.handle.close()
                self.pool.busy.remove(lease_uuid(self.gpu))

    def quarantine(self, reason):
        """Preserve uncertain device ownership in the existing lock, never unlock it by guess."""
        if self.handle is not None:
            self.handle.seek(0)
            self.handle.truncate(0)
            self.handle.write(
                json.dumps({"status": "QUARANTINED", "reason": reason}) + "\n"
            )
            self.handle.truncate()
            self.handle.flush()
            os.fsync(self.handle.fileno())

    @property
    def quarantined(self):
        return self.handle is not None and bool(os.pread(self.handle.fileno(), 1, 0))


class ResourcePool:
    def __init__(self, inventory, lock_dir, legacy_glob="/tmp/*.gpu.lock"):
        self.cpu = inventory["cpu_slots"]
        self.ram = inventory["ram_gb"]
        self.cloud = inventory.get("cloud_slots", 0)
        self.gpus = inventory.get("gpus", [])
        self.busy = set()
        self.guard = threading.Lock()
        self.directory = Path(lock_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.legacy_glob = legacy_glob

    def acquire(self, resources):
        with self.guard:
            if resources.get("cpu", 1) > self.cpu or resources["ram_gb"] > self.ram:
                return None
            kind = resources["kind"]
            if kind == "cloud" and self.cloud < 1:
                return None
            handle = gpu = None
            if kind in ("cuda", "rtx"):
                inherited = inherited_capability()
                if (
                    inherited is not None
                    and Path(inherited["lock"]).parent != self.directory
                ):
                    if self.directory == Path(DEFAULT_LOCK_DIR):
                        self.directory = Path(inherited["lock"]).parent
                    else:
                        raise RuntimeError(
                            "Nested GPU request must use the inherited lease directory"
                        )
                if (
                    inherited is not None
                    and kind not in inherited["gpu"]["capabilities"]
                ):
                    raise ValueError(
                        "Inherited GPU lease lacks the requested capability"
                    )
                if self.legacy_glob and glob.glob(self.legacy_glob):
                    return None
                for candidate in self.gpus:
                    if (
                        kind not in candidate["capabilities"]
                        or lease_uuid(candidate) in self.busy
                        or candidate["vram_gb"] < resources["vram_gb"]
                        or (
                            inherited is not None
                            and candidate["uuid"] != inherited["gpu"]["uuid"]
                        )
                    ):
                        continue
                    if inherited is not None:
                        if os.pread(inherited["lock_fd"], 1, 0):
                            raise RuntimeError(
                                "SIMULATION_RESOURCE_BUSY; GPU lease is quarantined"
                            )
                        handle = os.fdopen(os.dup(inherited["lock_fd"]), "a+")
                        gpu = inherited["gpu"]
                        self.busy.add(lease_uuid(gpu))
                        break
                    attempt = (self.directory / f"{lease_uuid(candidate)}.lease").open(
                        "a+"
                    )
                    try:
                        fcntl.flock(attempt.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        attempt.close()
                        continue
                    attempt.seek(0)
                    if attempt.read(1):
                        attempt.close()
                        raise RuntimeError(
                            "SIMULATION_RESOURCE_BUSY; GPU lease is quarantined"
                        )
                    handle, gpu = attempt, candidate
                    self.busy.add(lease_uuid(gpu))
                    break
                if handle is None:
                    return None
            try:
                lease = Lease(self, resources, gpu, handle)
            except BaseException:
                if handle is not None:
                    handle.close()
                    self.busy.remove(lease_uuid(gpu))
                raise
            self.cpu -= resources.get("cpu", 1)
            self.ram -= resources["ram_gb"]
            if kind == "cloud":
                self.cloud -= 1
            return lease
