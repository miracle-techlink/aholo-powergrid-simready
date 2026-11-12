"""Bounded subprocess DAG executor with verified, content-addressed artifacts."""

import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from ..artifacts import digest, object_digest, write_json
from ..process import ProcessTimeout, run_process
from .cache import cache_hit as _cache_hit
from .cache import hash_outputs as _hash_outputs
from .contracts import input_files, validate
from .resources import ResourcePool


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


def _command(task, inputs, results, output):
    replacements = {"python": sys.executable, "out": str(output)}
    replacements.update({f"input:{name}": str(path) for name, path in inputs.items()})
    for dep in task.get("deps", []):
        for name in results[dep]["outputs"]:
            replacements[f"dep:{dep}:{name}"] = str(
                Path(results[dep]["artifact_dir"]) / name
            )

    def replace(match):
        key = match.group(1)
        if key not in replacements:
            raise ValueError(f"Unknown command placeholder: {key}")
        return replacements[key]

    return [
        re.sub(r"\{(python|out|input:[^{}]+|dep:[^{}]+)\}", replace, arg)
        for arg in task["argv"]
    ]


def _execute(task, prepared, results, cache, task_dir, lease, queue_s):
    started = time.monotonic()
    inputs, hashes, dependencies, key = prepared
    output = task_dir / "files"
    record = dict(
        task_id=task["id"],
        scene_id=task["scene_id"],
        stage=task["stage"],
        key=key,
        status="FAILED",
        queue_seconds=queue_s,
        process_seconds=0.0,
        cache_hit=False,
        gpu_uuid=lease.gpu["uuid"] if lease.gpu else None,
        provider_cost=None,
        token_usage=None,
        peak_vram_bytes=None,
    )
    try:
        output.mkdir(parents=True)
        if {name: digest(path) for name, path in inputs.items()} != hashes:
            raise ValueError("Input changed while waiting for resources")
        for dep, expected in dependencies.items():
            if (
                _hash_outputs(results[dep]["artifact_dir"], list(expected["outputs"]))
                != expected["outputs"]
            ):
                raise ValueError("Dependency changed while waiting for resources")
        command = _command(task, inputs, results, output)
        env = dict(os.environ)
        env.update(task.get("env", {}))
        env["SIMREADY_OUT"] = str(output)
        env["SIMREADY_SEED"] = str(task.get("seed", 0))
        # A declared CPU/cloud task cannot accidentally initialize a CUDA model.
        env["CUDA_VISIBLE_DEVICES"] = lease.gpu["uuid"] if lease.gpu else ""
        if lease.gpu:
            env["SIMREADY_GPU_UUID"] = lease.gpu["uuid"]
        try:
            elapsed = run_process(
                command,
                cwd=output,
                env=env,
                timeout=task["timeout_s"],
                stdout=task_dir / "stdout.log",
                stderr=task_dir / "stderr.log",
                pass_fds=lease.pass_fds,
            )
            code = elapsed.returncode
            record["process_seconds"] = elapsed.seconds
        except ProcessTimeout as error:
            record.update(status="TIMEOUT", process_seconds=error.seconds)
            raise
        record["returncode"] = code
        if code != 0:
            raise ValueError(f"Worker exited with code {code}; see stderr.log")
        if {name: digest(path) for name, path in inputs.items()} != hashes:
            raise ValueError("Input changed while worker was running")
        for dep, expected in dependencies.items():
            if (
                _hash_outputs(results[dep]["artifact_dir"], list(expected["outputs"]))
                != expected["outputs"]
            ):
                raise ValueError("Dependency changed while worker was running")
        produced = _hash_outputs(output, task["outputs"])
        artifacts = str(output)
        if task.get("cache", True):
            # Publish only complete declared files. Corrupt entries are never
            # overwritten or silently repaired: use a new cache root to recover.
            destination = cache / key
            if destination.exists():
                existing = _cache_hit(cache, key, task["outputs"])
                if existing is None:
                    raise ValueError(
                        "Corrupt cache entry; preserve it for inspection and use a new cache root"
                    )
                if existing["outputs"] != produced:
                    raise ValueError(
                        "Same cache key produced different outputs; disable caching or fix identity"
                    )
                artifacts = existing["artifact_dir"]
            else:
                staging = Path(tempfile.mkdtemp(prefix=".publish-", dir=cache))
                try:
                    for name in task["outputs"]:
                        target = staging / "files" / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(output / name, target)
                    if _hash_outputs(staging / "files", task["outputs"]) != produced:
                        raise ValueError("Output changed during cache publication")
                    write_json(
                        staging / "manifest.json", {"key": key, "outputs": produced}
                    )
                    try:
                        staging.rename(destination)
                    except OSError:
                        existing = _cache_hit(cache, key, task["outputs"])
                        if existing is None or existing["outputs"] != produced:
                            raise ValueError("Concurrent cache publication conflict")
                    artifacts = str(destination / "files")
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
        record.update(status="SUCCESS", outputs=produced, artifact_dir=artifacts)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        lease.close()
        record["worker_wall_seconds"] = time.monotonic() - started
        # Missing output directory can itself be the original worker error.
        write_json(task_dir / "result.json", record)
    return record


def run(
    plan,
    inventory,
    base,
    out,
    cache,
    lock_dir="/tmp/simready-device-leases",
    max_workers=8,
    legacy_glob="/tmp/*.gpu.lock",
    wall_timeout_s=None,
):
    """Run one trusted local plan. Re-run in a NEW output directory to resume via cache."""
    base, out, cache = Path(base).resolve(), Path(out).resolve(), Path(cache).resolve()
    validate(plan, inventory, base)
    if wall_timeout_s is not None:
        from .contracts import number

        number(wall_timeout_s, "wall_timeout_s", 0.001)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    out.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    pool = ResourcePool(inventory, lock_dir, legacy_glob)
    engine_files = list(Path(__file__).parent.glob("*.py")) + [
        Path(__file__).parents[1] / name for name in ("artifacts.py", "process.py")
    ]
    engine = object_digest({p.name: digest(p) for p in engine_files})
    started = time.monotonic()
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
                        _execute,
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
        tasks=results,
        cache_hits=sum(r.get("cache_hit", False) for r in results.values()),
        release_authorized=False,
    )
    write_json(out / "result.json", result)
    return result
