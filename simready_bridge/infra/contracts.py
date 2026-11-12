"""Explicit task/resource contracts; paths resolve relative to the plan file."""

import math
import re
from pathlib import Path, PurePosixPath


def number(value, name, minimum=0):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def relative_output(value):
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
        or value == "."
    ):
        raise ValueError(f"Output must be a normalized relative file: {value!r}")
    return value


def validate(plan, inventory, base):
    if plan.get("schema_version") != 1 or inventory.get("schema_version") != 1:
        raise ValueError("Expected schema_version=1")
    number(inventory["cpu_slots"], "cpu_slots", 1)
    if not isinstance(inventory["cpu_slots"], int):
        raise ValueError("cpu_slots must be an integer")
    number(inventory["ram_gb"], "ram_gb", 0.001)
    number(inventory.get("cloud_slots", 0), "cloud_slots")
    if not isinstance(inventory.get("cloud_slots", 0), int):
        raise ValueError("cloud_slots must be an integer")
    gpus = inventory.get("gpus", [])
    uuids = set()
    for gpu in gpus:
        uuid = gpu["uuid"]
        if not re.fullmatch(r"(GPU|MIG)-[a-zA-Z0-9-]+", uuid) or uuid in uuids:
            raise ValueError("GPU UUIDs must be unique physical/device identifiers")
        uuids.add(uuid)
        number(gpu["vram_gb"], "vram_gb", 0.001)
        if not set(gpu["capabilities"]) <= {"cuda", "rtx"}:
            raise ValueError("Unknown accelerator capability")
        if "rtx" in gpu["capabilities"] and gpu.get("rtx_isolated") is not True:
            raise ValueError(
                "RTX needs externally isolated Vulkan/renderer device binding"
            )
    tasks = plan["tasks"]
    if not tasks:
        raise ValueError("Empty plan")
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids) or any(
        not re.fullmatch(r"[a-zA-Z0-9_-]+", x) for x in ids
    ):
        raise ValueError("Task IDs must be unique safe names")
    for t in tasks:
        if not t.get("scene_id") or not t.get("stage") or not t.get("runtime_identity"):
            raise ValueError("scene_id, stage and pinned runtime_identity are required")
        if (
            not isinstance(t.get("argv"), list)
            or not t["argv"]
            or any(not isinstance(a, str) for a in t["argv"])
        ):
            raise ValueError("argv must be a nonempty string list (no shell)")
        if not t.get("outputs") or len(set(t["outputs"])) != len(t["outputs"]):
            raise ValueError("Declare unique output files")
        for name in t["outputs"]:
            relative_output(name)
        deps = t.get("deps", [])
        if len(set(deps)) != len(deps) or not set(deps) <= set(ids) or t["id"] in deps:
            raise ValueError("Invalid dependencies")
        for name, path in t.get("inputs", {}).items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or not (base / path).is_file():
                raise ValueError(f"Invalid or missing input: {name}")
        kind = t["resources"]["kind"]
        if kind not in ("cpu", "cuda", "rtx", "cloud"):
            raise ValueError("Unknown resource kind")
        cpu = number(t["resources"].get("cpu", 1), "cpu", 1)
        ram = number(t["resources"]["ram_gb"], "ram_gb", 0.001)
        if (
            not isinstance(cpu, int)
            or cpu > inventory["cpu_slots"]
            or ram > inventory["ram_gb"]
        ):
            raise ValueError(f"Task {t['id']} exceeds host capacity")
        if kind in ("cuda", "rtx"):
            vram = number(t["resources"]["vram_gb"], "vram_gb", 0.001)
            if not any(
                kind in gpu["capabilities"] and gpu["vram_gb"] >= vram for gpu in gpus
            ):
                raise ValueError(f"No compatible device for {t['id']}")
        if kind == "cloud" and (
            inventory.get("cloud_slots", 0) < 1 or t.get("cache", True)
        ):
            raise ValueError(
                "Cloud tasks require quota and explicit cache=false; no automatic retries"
            )
        if not isinstance(t.get("cache", True), bool):
            raise ValueError("cache must be boolean")
        number(t["timeout_s"], "timeout_s", 0.001)
        number(t.get("queue_timeout_s", 300), "queue_timeout_s", 0.001)
        env = t.get("env", {})
        if any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError("env values must be strings")
        if any(
            k in env
            for k in (
                "CUDA_VISIBLE_DEVICES",
                "SIMREADY_GPU_UUID",
                "SIMREADY_OUT",
                "SIMREADY_SEED",
            )
        ):
            raise ValueError(
                "Task cannot override scheduler device/output/seed bindings"
            )
    complete = set()
    while len(complete) < len(ids):
        ready = {
            t["id"] for t in tasks if set(t.get("deps", [])) <= complete
        } - complete
        if not ready:
            raise ValueError("Dependency cycle")
        complete |= ready


def input_files(task, base):
    return {
        name: (Path(base) / path).resolve()
        for name, path in task.get("inputs", {}).items()
    }
