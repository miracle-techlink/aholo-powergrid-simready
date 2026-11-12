"""Bounded subprocess DAG executor with verified, content-addressed artifacts."""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from simready.core.artifacts import digest, object_digest, write_json
from simready.core.types import JsonObject, PathLike
from simready.execution.artifact_cache import cache_hit as _cache_hit
from simready.execution.artifact_cache import hash_outputs as _hash_outputs
from simready.execution.resource_pool import ResourcePool
from simready.execution.task_schema import input_files, validate
from simready.execution.task_worker import execute_task


def _prepare(task, base, results, engine_identity):
    inputs = input_files(task, base)
    hashes = {name: digest(path) for name, path in inputs.items()}
    dependencies = {}
    for dep in task.get("deps", []):
        record = results[dep]
        if (
            _hash_outputs(record["artifact_dir"], list(record["outputs"]))
            != record["outputs"]
        ):
            raise ValueError(f"Dependency artifacts changed: {dep}")
        dependencies[dep] = {"key": record["key"], "outputs": record["outputs"]}
    identity = {
        name: task.get(name)
        for name in (
            "scene_id",
            "stage",
            "argv",
            "outputs",
            "runtime_identity",
            "parameters",
            "seed",
            "env",
            "quality_profile",
        )
    }
    identity.update(
        engine_identity=engine_identity,
        inputs=hashes,
        dependencies=dependencies,
        accelerator=task["resources"]["kind"],
    )
    return inputs, hashes, dependencies, object_digest(identity)


def run_task_graph(
    plan: JsonObject,
    inventory: JsonObject,
    base_dir: PathLike,
    output_dir: PathLike,
    cache_dir: PathLike,
    lock_dir: PathLike = "/tmp/simready-device-leases",
    max_workers: int = 8,
    legacy_glob: str = "/tmp/*.gpu.lock",
    wall_timeout_s: float | None = None,
) -> JsonObject:
    """Run one trusted local plan. Re-run in a NEW output directory to resume via cache."""
    base, out, cache = (
        Path(base_dir).resolve(),
        Path(output_dir).resolve(),
        Path(cache_dir).resolve(),
    )
    validate(plan, inventory, base)
    if wall_timeout_s is not None:
        from simready.core.validation import number

        number(wall_timeout_s, "wall_timeout_s", 0.001)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    out.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    pool = ResourcePool(inventory, lock_dir, legacy_glob)
    engine_files = list(Path(__file__).parent.glob("*.py")) + [
        Path(__file__).parents[1] / "core" / "artifacts.py",
        Path(__file__).parents[1] / "core" / "validation.py",
    ]
    engine = object_digest({p.name: digest(p) for p in engine_files})
    started = time.monotonic()
    started_unix_s = time.time()
    deadline = started + wall_timeout_s if wall_timeout_s is not None else float("inf")
    pending = {t["id"]: t for t in plan["tasks"]}
    results, running, ready_at, prepared = {}, {}, {}, {}
    write_json(out / "plan.json", plan)
    write_json(out / "inventory.json", inventory)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending or running:
            for future in list(running):
                if future.done():
                    task_id = running.pop(future)
                    results[task_id] = future.result()
                    write_json(out / "progress.json", results)
            for task_id, task in list(pending.items()):
                if time.monotonic() >= deadline:
                    results[task_id] = {
                        "task_id": task_id,
                        "status": "TIMEOUT",
                        "error": "DAG wall deadline exceeded",
                    }
                    del pending[task_id]
                    continue
                deps = task.get("deps", [])
                if any(
                    d in results and results[d]["status"] != "SUCCESS" for d in deps
                ):
                    results[task_id] = {
                        "task_id": task_id,
                        "status": "SKIPPED",
                        "error": "Dependency failed",
                    }
                    del pending[task_id]
                    continue
                if not all(d in results for d in deps):
                    continue
                ready_at.setdefault(task_id, time.monotonic())
                queued = time.monotonic() - ready_at[task_id]
                try:
                    if task_id not in prepared:
                        prepared[task_id] = _prepare(task, base, results, engine)
                    key = prepared[task_id][3]
                    hit = (
                        _cache_hit(cache, key, task["outputs"])
                        if task.get("cache", True)
                        else None
                    )
                    if hit:
                        inputs, hashes, deps_snapshot, _ = prepared[task_id]
                        if {
                            name: digest(path) for name, path in inputs.items()
                        } != hashes:
                            raise ValueError("Input changed before cache reuse")
                        for dep, expected in deps_snapshot.items():
                            if (
                                _hash_outputs(
                                    results[dep]["artifact_dir"],
                                    list(expected["outputs"]),
                                )
                                != expected["outputs"]
                            ):
                                raise ValueError(
                                    "Dependency changed before cache reuse"
                                )
                        results[task_id] = dict(
                            hit,
                            task_id=task_id,
                            scene_id=task["scene_id"],
                            stage=task["stage"],
                            status="SUCCESS",
                            key=key,
                            cache_hit=True,
                            queue_seconds=queued,
                            process_seconds=0.0,
                            provider_cost=None,
                            token_usage=None,
                        )
                        del pending[task_id]
                        continue
                    if (cache / key).exists() and task.get("cache", True):
                        raise ValueError(
                            "Corrupt cache entry; use a new cache root after inspection"
                        )
                    if queued > task.get("queue_timeout_s", 300):
                        raise TimeoutError("Resource queue deadline exceeded")
                    if len(running) >= max_workers:
                        continue
                    lease = pool.acquire(task["resources"])
                    if lease is None:
                        continue
                    task = dict(
                        task,
                        timeout_s=min(
                            task["timeout_s"], max(0.001, deadline - time.monotonic())
                        ),
                    )
                    future = executor.submit(
                        execute_task,
                        task,
                        prepared[task_id],
                        dict(results),
                        cache,
                        out / task_id,
                        lease,
                        queued,
                    )
                    running[future] = task_id
                    del pending[task_id]
                except (ValueError, OSError, TimeoutError) as error:
                    results[task_id] = {
                        "task_id": task_id,
                        "status": "FAILED",
                        "error": str(error),
                    }
                    del pending[task_id]
            if running:
                wait(running, timeout=0.05, return_when=FIRST_COMPLETED)
            elif pending:
                time.sleep(0.05)
    result = dict(
        schema_version=1,
        status="SUCCESS"
        if all(r["status"] == "SUCCESS" for r in results.values())
        else "FAILED",
        wall_seconds=time.monotonic() - started,
        started_unix_s=started_unix_s,
        ended_unix_s=time.time(),
        tasks=results,
        cache_hits=sum(r.get("cache_hit", False) for r in results.values()),
        release_authorized=False,
    )
    write_json(out / "result.json", result)
    return result
