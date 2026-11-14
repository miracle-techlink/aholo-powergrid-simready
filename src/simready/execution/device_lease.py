"""Adapt explicit device selections to the existing physical-UUID ResourcePool."""

import copy
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from simready.core.validation import number
from simready.execution.gpu_capability import inherited_capability
from simready.execution.processes import run_process
from simready.execution.resource_pool import DEFAULT_LOCK_DIR, ResourcePool


def discover_inventory():
    """Read local device identity; discovery failure never authorizes an unlocked GPU."""
    with tempfile.TemporaryDirectory(prefix="simready-devices-") as directory:
        root = Path(directory)
        try:
            result = run_process(
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                cwd=None,
                timeout=5,
                stdout=root / "stdout",
                stderr=root / "stderr",
            )
        except OSError as error:
            raise RuntimeError(
                "GPU discovery unavailable; provide a reviewed device inventory"
            ) from error
        if result.returncode:
            raise RuntimeError(
                "GPU discovery failed; provide a reviewed device inventory"
            )
        devices = []
        for line in (root / "stdout").read_text().splitlines():
            index, uuid, memory = (value.strip() for value in line.split(","))
            known_capacity = memory not in {"[N/A]", "N/A"}
            try:
                # Unified-memory devices may expose identity without dedicated
                # VRAM capacity. Zero permits the exclusive launcher lease but
                # conservatively rejects any positive memory reservation.
                capacity = (
                    number(float(memory), "GPU memory.total", 0) / 1024
                    if known_capacity
                    else 0.0
                )
            except ValueError as error:
                raise RuntimeError(
                    "Invalid GPU memory.total; provide a reviewed device inventory"
                ) from error
            devices.append(
                dict(
                    index=int(index),
                    uuid=uuid,
                    vram_gb=capacity,
                    vram_capacity_known=known_capacity,
                    capabilities=["cuda", "rtx"],
                )
            )
        if not devices:
            raise RuntimeError("No physical GPU identity discovered")
        for device in devices:
            device["rtx_isolated"] = len(devices) == 1
        return dict(schema_version=1, cpu_slots=1, ram_gb=1, gpus=devices)


def _validated_inventory(value):
    inventory = copy.deepcopy(value)
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise ValueError("Expected device inventory schema_version=1")
    number(inventory.get("cpu_slots"), "cpu_slots", 1)
    number(inventory.get("ram_gb"), "ram_gb", 0.001)
    if type(inventory["cpu_slots"]) is not int:
        raise ValueError("cpu_slots must be an integer")
    uuids, indices = set(), set()
    for index, device in enumerate(inventory.get("gpus", [])):
        uuid = device.get("uuid")
        ordinal = device.setdefault("index", index)
        if (
            not isinstance(uuid, str)
            or not re.fullmatch(r"GPU-[A-Za-z0-9-]+", uuid)
            or uuid in uuids
        ):
            raise ValueError("Unique physical GPU UUIDs are required")
        if type(ordinal) is not int or ordinal < 0 or ordinal in indices:
            raise ValueError("Unique nonnegative GPU indices are required")
        number(device.get("vram_gb"), "vram_gb", 0)
        if not isinstance(device.get("capabilities"), list) or not set(
            device["capabilities"]
        ) <= {"cuda", "rtx"}:
            raise ValueError("Unknown GPU capabilities")
        uuids.add(uuid)
        indices.add(ordinal)
    return inventory


@contextmanager
def device_lease(
    *,
    resource_kind="rtx",
    gpu="0",
    device_inventory=None,
    lock_dir=DEFAULT_LOCK_DIR,
    container=False,
):
    """Borrow verified inherited ownership, or acquire one physical device once."""
    if resource_kind not in {"cpu", "cuda", "rtx"}:
        raise ValueError("resource_kind must be cpu, cuda or rtx")
    if resource_kind != "cpu" and list(Path(lock_dir).glob("*.gpu.lock")):
        raise RuntimeError("SIMULATION_RESOURCE_BUSY; existing locks are retained")
    if resource_kind == "cpu":
        inventory = dict(cpu_slots=1, ram_gb=1, gpus=[])
        resources = dict(kind="cpu", ram_gb=0.001)
    else:
        inherited = inherited_capability()
        if inherited is not None:
            device = inherited["gpu"]
            if str(gpu) not in {"0", device["uuid"], str(device.get("index", 0))}:
                raise ValueError("Nested GPU request cannot select another device")
            inherited_directory = Path(inherited["lock"]).parent
            if (
                Path(lock_dir).resolve() != Path(DEFAULT_LOCK_DIR)
                and Path(lock_dir).resolve() != inherited_directory
            ):
                raise ValueError("Nested GPU request cannot change its lease directory")
            lock_dir = inherited_directory
            inventory = dict(schema_version=1, cpu_slots=1, ram_gb=1, gpus=[device])
        else:
            inventory = _validated_inventory(
                device_inventory
                if device_inventory is not None
                else discover_inventory()
            )
            selected = [
                device
                for device in inventory.get("gpus", [])
                if str(gpu) in {device["uuid"], str(device["index"])}
            ]
            if len(selected) != 1:
                raise ValueError("GPU selection must resolve to one physical UUID")
            inventory["gpus"] = selected
            device = selected[0]
        if resource_kind not in device["capabilities"]:
            raise ValueError("Selected GPU lacks the declared capability")
        if resource_kind == "rtx" and not container and not device.get("rtx_isolated"):
            raise ValueError("Local RTX requires a reviewed isolated renderer device")
        resources = dict(kind=resource_kind, ram_gb=0.001, vram_gb=0)
    # Respect both historical global locks and a caller's old custom lock scope.
    if resource_kind != "cpu" and list(Path(lock_dir).glob("*.gpu.lock")):
        raise RuntimeError("SIMULATION_RESOURCE_BUSY; existing locks are retained")
    pool = ResourcePool(inventory, lock_dir)
    lease = pool.acquire(resources)
    if lease is None:
        raise RuntimeError("SIMULATION_RESOURCE_BUSY; physical GPU lease unavailable")
    try:
        yield lease
    finally:
        lease.close()
