"""Bounded subprocess DAG executor with verified, content-addressed artifacts."""

import copy
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event

from simready.core.artifacts import digest, object_digest, write_json
from simready.core.types import JsonObject, PathLike
from simready.execution.artifact_cache import cache_hit as _cache_hit
from simready.execution.artifact_cache import hash_outputs as _hash_outputs
from simready.execution.resource_pool import ResourcePool
from simready.execution.task_environment import freeze_environment, plan_receipt
from simready.execution.task_schema import input_files, validate
from simready.execution.task_worker import execute_task


def _prepare(task, base, results, engine_identity, environment):
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
            "quality_profile",
        )
    }
    identity.update(
        engine_identity=engine_identity,
        inputs=hashes,
        dependencies=dependencies,
        accelerator=task["resources"]["kind"],
        environment=environment.identity(),
    )
    return inputs, hashes, dependencies, object_digest(identity), environment


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
    """Run a trusted plan; explicit nonsecret environments permit verified reuse.

    Re-run in a NEW output directory to resume via cache. Legacy v1 tasks without
    an environment contract still inherit the host, but artifact caching is off.
    """
    base, out, cache = (
        Path(base_dir).resolve(),
        Path(output_dir).resolve(),
        Path(cache_dir).resolve(),
    )
    plan = copy.deepcopy(plan)
    validate(plan, inventory, base)
    if wall_timeout_s is not None:
        from simready.core.validation import number

        number(wall_timeout_s, "wall_timeout_s", 0.001)
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    host_environment = dict(os.environ)
    environments = {
        task["id"]: freeze_environment(task, host_environment) for task in plan["tasks"]
    }
    del host_environment
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
    write_json(out / "plan.json", plan_receipt(plan))
    write_json(out / "inventory.json", inventory)
    cancellation = Event()
    failure = None
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        while pending or running:
            for future in list(running):
                if future.done():
                    task_id = running[future]
                    results[task_id] = future.result()
                    del running[future]
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
                        prepared[task_id] = _prepare(
                            task, base, results, engine, environments[task_id]
                        )
                    key = prepared[task_id][3]
                    environment = environments[task_id]
                    hit = (
                        _cache_hit(cache, key, task["outputs"])
                        if environment.cache_enabled
                        else None
                    )
                    if hit:
                        inputs, hashes, deps_snapshot, _, _ = prepared[task_id]
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
                            **environment.receipt(),
                        )
                        del pending[task_id]
                        continue
                    if (cache / key).exists() and environment.cache_enabled:
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
                    try:
                        future = executor.submit(
                            execute_task,
                            task,
                            prepared[task_id],
                            dict(results),
                            cache,
                            out / task_id,
                            lease,
                            queued,
                            cancel_event=cancellation,
                        )
                    except BaseException:
                        lease.close()
                        raise
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
    except BaseException as error:
        failure = error
        # Cancel owned process groups BEFORE joining worker threads. Otherwise
        # Ctrl-C waits for each task's original timeout and can leave no receipt.
        cancellation.set()
        raise
    finally:
        executor.shutdown(wait=True)
        for future, task_id in running.items():
            try:
                results[task_id] = future.result()
            except BaseException as error:
                results[task_id] = {
                    "task_id": task_id,
                    "status": "FAILED",
                    "error": f"{type(error).__name__}: {error}",
                }
        for task_id in pending:
            results[task_id] = {
                "task_id": task_id,
                "status": "CANCELLED",
                "error": "Task graph stopped before dispatch",
            }
        for task_id, record in results.items():
            record.update(environments[task_id].receipt())
        status = (
            "SUCCESS"
            if all(r["status"] == "SUCCESS" for r in results.values())
            else "FAILED"
        )
        if failure is not None:
            status = "FAILED" if isinstance(failure, Exception) else "INTERRUPTED"
        result = dict(
            schema_version=1,
            status=status,
            wall_seconds=time.monotonic() - started,
            started_unix_s=started_unix_s,
            ended_unix_s=time.time(),
            tasks=results,
            cache_hits=sum(r.get("cache_hit", False) for r in results.values()),
            release_authorized=False,
        )
        if failure is not None:
            result["error"] = f"{type(failure).__name__}: {failure}"
        write_json(out / "result.json", result)
    return result
