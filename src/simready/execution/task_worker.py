"""Execute one prepared task, verify its outputs and publish its cache entry."""

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from simready.core.artifacts import digest, write_json
from simready.execution.artifact_cache import cache_hit as _cache_hit
from simready.execution.artifact_cache import hash_outputs as _hash_outputs
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)


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


def execute_task(
    task, prepared, results, cache, task_dir, lease, queue_s, cancel_event=None
):
    started = time.monotonic()
    inputs, hashes, dependencies, key, environment = prepared
    output = task_dir / "files"
    record = dict(
        task_id=task["id"],
        started_unix_s=time.time(),
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
        **environment.receipt(),
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
        env = dict(environment.values)
        env["SIMREADY_OUT"] = str(output)
        env["SIMREADY_SEED"] = str(task.get("seed", 0))
        # A declared CPU/cloud task cannot accidentally initialize a CUDA model.
        env["CUDA_VISIBLE_DEVICES"] = lease.gpu["uuid"] if lease.gpu else ""
        if lease.gpu:
            env["SIMREADY_GPU_UUID"] = lease.gpu["uuid"]
            env.update(lease.environment)
        else:
            env.pop("SIMREADY_GPU_UUID", None)
        try:
            elapsed = run_process(
                command,
                cwd=output,
                env=env,
                timeout=task["timeout_s"],
                stdout=task_dir / "stdout.log",
                stderr=task_dir / "stderr.log",
                pass_fds=lease.pass_fds,
                cancel_event=cancel_event,
            )
            code = elapsed.returncode
            record["process_seconds"] = elapsed.seconds
        except ProcessTimeoutError as error:
            record.update(status="TIMEOUT", process_seconds=error.seconds)
            raise
        except ProcessCancelledError as error:
            record.update(status="CANCELLED", process_seconds=error.seconds)
            raise
        record["returncode"] = code
        if code != 0:
            raise ValueError(f"Worker exited with code {code}; see stderr.log")
        if lease.quarantined:
            raise ValueError("GPU lease was quarantined by a nested worker")
        if {name: digest(path) for name, path in inputs.items()} != hashes:
            raise ValueError("Input changed while worker was running")
        for dep, expected in dependencies.items():
            if (
                _hash_outputs(results[dep]["artifact_dir"], list(expected["outputs"]))
                != expected["outputs"]
            ):
                raise ValueError("Dependency changed while worker was running")
        produced = _hash_outputs(output, task["outputs"])
        if cancel_event is not None and cancel_event.is_set():
            record["status"] = "CANCELLED"
            raise ProcessCancelledError(time.monotonic() - started)
        artifacts = str(output)
        if environment.cache_enabled:
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
                    except OSError as error:
                        existing = _cache_hit(cache, key, task["outputs"])
                        if existing is None or existing["outputs"] != produced:
                            raise ValueError(
                                "Concurrent cache publication conflict"
                            ) from error
                    artifacts = str(destination / "files")
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
        record.update(status="SUCCESS", outputs=produced, artifact_dir=artifacts)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        lease.close()
        record["ended_unix_s"] = time.time()
        record["worker_wall_seconds"] = time.monotonic() - started
        # Missing output directory can itself be the original worker error.
        write_json(task_dir / "result.json", record)
    return record
