"""Bounded subprocess DAG executor with verified, content-addressed artifacts."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from ..artifacts import digest, object_digest, write_json
from .contracts import input_files, validate
from .resources import ResourcePool


def _hash_outputs(directory, names):
    directory = Path(directory).resolve()
    result = {}
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file():
            raise ValueError(f'Missing or escaping output: {name}')
        result[name] = digest(path)
    return result


def _cache_hit(cache, key, outputs):
    directory = cache / key
    try:
        record = json.loads((directory / 'manifest.json').read_text())
        hashes = _hash_outputs(directory / 'files', outputs)
        if record['key'] == key and record['outputs'] == hashes:
            return {'artifact_dir': str(directory / 'files'), 'outputs': hashes}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _prepare(task, base, results, engine_identity):
    inputs = input_files(task, base)
    hashes = {name: digest(path) for name, path in inputs.items()}
    dependencies = {}
    for dep in task.get('deps', []):
        record = results[dep]
        if _hash_outputs(record['artifact_dir'], list(record['outputs'])) != record['outputs']:
            raise ValueError(f'Dependency artifacts changed: {dep}')
        dependencies[dep] = {'key': record['key'], 'outputs': record['outputs']}
    identity = {name: task.get(name) for name in (
        'scene_id', 'stage', 'argv', 'outputs', 'runtime_identity', 'parameters', 'seed', 'env', 'quality_profile')}
    identity.update(engine_identity=engine_identity, inputs=hashes, dependencies=dependencies,
                    accelerator=task['resources']['kind'])
    return inputs, hashes, dependencies, object_digest(identity)


def _command(task, inputs, results, output):
    replacements = {'python': sys.executable, 'out': str(output)}
    replacements.update({f'input:{name}': str(path) for name, path in inputs.items()})
    for dep in task.get('deps', []):
        for name in results[dep]['outputs']:
            replacements[f'dep:{dep}:{name}'] = str(Path(results[dep]['artifact_dir']) / name)

    def replace(match):
        key = match.group(1)
        if key not in replacements:
            raise ValueError(f'Unknown command placeholder: {key}')
        return replacements[key]

    return [re.sub(r'\{(python|out|input:[^{}]+|dep:[^{}]+)\}', replace, arg) for arg in task['argv']]


def _execute(task, prepared, results, cache, task_dir, lease, queue_s):
    started = time.monotonic()
    inputs, hashes, dependencies, key = prepared
    output = task_dir / 'files'
    record = dict(task_id=task['id'], scene_id=task['scene_id'], stage=task['stage'], key=key,
                  status='FAILED', queue_seconds=queue_s, process_seconds=0.0,
                  cache_hit=False, gpu_uuid=lease.gpu['uuid'] if lease.gpu else None,
                  provider_cost=None, token_usage=None, peak_vram_bytes=None)
    process = None
    try:
        output.mkdir(parents=True)
        if {name: digest(path) for name, path in inputs.items()} != hashes:
            raise ValueError('Input changed while waiting for resources')
        for dep, expected in dependencies.items():
            if _hash_outputs(results[dep]['artifact_dir'], list(expected['outputs'])) != expected['outputs']:
                raise ValueError('Dependency changed while waiting for resources')
        command = _command(task, inputs, results, output)
        env = dict(os.environ)
        env.update(task.get('env', {}))
        env['SIMREADY_OUT'] = str(output)
        env['SIMREADY_SEED'] = str(task.get('seed', 0))
        # A declared CPU/cloud task cannot accidentally initialize a CUDA model.
        env['CUDA_VISIBLE_DEVICES'] = lease.gpu['uuid'] if lease.gpu else ''
        if lease.gpu:
            env['SIMREADY_GPU_UUID'] = lease.gpu['uuid']
        with (task_dir / 'stdout.log').open('wb') as stdout, (task_dir / 'stderr.log').open('wb') as stderr:
            process_started = time.monotonic()
            process = subprocess.Popen(command, cwd=output, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=True, pass_fds=lease.pass_fds)
            try:
                code = process.wait(timeout=task['timeout_s'])
            except subprocess.TimeoutExpired:
                record['status'] = 'TIMEOUT'
                raise TimeoutError(f'Task exceeded {task["timeout_s"]} seconds')
            finally:
                record['process_seconds'] = time.monotonic() - process_started
                # Commands must be foreground workers. Clean up only this task's
                # process group, including descendants left after a normal exit.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        record['returncode'] = code
        if code != 0:
            raise ValueError(f'Worker exited with code {code}; see stderr.log')
        if {name: digest(path) for name, path in inputs.items()} != hashes:
            raise ValueError('Input changed while worker was running')
        for dep, expected in dependencies.items():
            if _hash_outputs(results[dep]['artifact_dir'], list(expected['outputs'])) != expected['outputs']:
                raise ValueError('Dependency changed while worker was running')
        produced = _hash_outputs(output, task['outputs'])
        artifacts = str(output)
        if task.get('cache', True):
            # Publish only complete declared files. Corrupt entries are never
            # overwritten or silently repaired: use a new cache root to recover.
            destination = cache / key
            if destination.exists():
                existing = _cache_hit(cache, key, task['outputs'])
                if existing is None:
                    raise ValueError('Corrupt cache entry; preserve it for inspection and use a new cache root')
                if existing['outputs'] != produced:
                    raise ValueError('Same cache key produced different outputs; disable caching or fix identity')
                artifacts = existing['artifact_dir']
            else:
                staging = Path(tempfile.mkdtemp(prefix='.publish-', dir=cache))
                try:
                    for name in task['outputs']:
                        target = staging / 'files' / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(output / name, target)
                    write_json(staging / 'manifest.json', {'key': key, 'outputs': produced})
                    try:
                        staging.rename(destination)
                    except OSError:
                        existing = _cache_hit(cache, key, task['outputs'])
                        if existing is None or existing['outputs'] != produced:
                            raise ValueError('Concurrent cache publication conflict')
                    artifacts = str(destination / 'files')
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
        record.update(status='SUCCESS', outputs=produced, artifact_dir=artifacts)
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
    finally:
        lease.close()
        record['worker_wall_seconds'] = time.monotonic() - started
        # Missing output directory can itself be the original worker error.
        write_json(task_dir / 'result.json', record)
    return record


def run(plan, inventory, base, out, cache, lock_dir='/tmp/simready-device-leases', max_workers=8,
        legacy_glob='/tmp/*.gpu.lock'):
    """Run one trusted local plan. Re-run in a NEW output directory to resume via cache."""
    base, out, cache = Path(base).resolve(), Path(out).resolve(), Path(cache).resolve()
    validate(plan, inventory, base)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError('max_workers must be a positive integer')
    out.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    pool = ResourcePool(inventory, lock_dir, legacy_glob)
    engine_files = list(Path(__file__).parent.glob('*.py')) + [Path(__file__).parents[1] / 'artifacts.py']
    engine = object_digest({p.name: digest(p) for p in engine_files})
    started = time.monotonic()
    pending = {t['id']: t for t in plan['tasks']}
    results, running, ready_at, prepared = {}, {}, {}, {}
    write_json(out / 'plan.json', plan)
    write_json(out / 'inventory.json', inventory)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending or running:
            for future in list(running):
                if future.done():
                    task_id = running.pop(future)
                    results[task_id] = future.result()
                    write_json(out / 'progress.json', results)
            for task_id, task in list(pending.items()):
                deps = task.get('deps', [])
                if any(d in results and results[d]['status'] != 'SUCCESS' for d in deps):
                    results[task_id] = {'task_id': task_id, 'status': 'SKIPPED', 'error': 'Dependency failed'}
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
                    hit = _cache_hit(cache, key, task['outputs']) if task.get('cache', True) else None
                    if hit:
                        results[task_id] = dict(hit, task_id=task_id, scene_id=task['scene_id'], stage=task['stage'],
                                               status='SUCCESS', key=key, cache_hit=True, queue_seconds=queued,
                                               process_seconds=0.0, provider_cost=None, token_usage=None)
                        del pending[task_id]
                        continue
                    if (cache / key).exists() and task.get('cache', True):
                        raise ValueError('Corrupt cache entry; use a new cache root after inspection')
                    if queued > task.get('queue_timeout_s', 300):
                        raise TimeoutError('Resource queue deadline exceeded')
                    if len(running) >= max_workers:
                        continue
                    lease = pool.acquire(task['resources'])
                    if lease is None:
                        continue
                    future = executor.submit(_execute, task, prepared[task_id], dict(results), cache,
                                             out / task_id, lease, queued)
                    running[future] = task_id
                    del pending[task_id]
                except (ValueError, OSError, TimeoutError) as error:
                    results[task_id] = {'task_id': task_id, 'status': 'FAILED', 'error': str(error)}
                    del pending[task_id]
            if running:
                wait(running, timeout=.05, return_when=FIRST_COMPLETED)
            elif pending:
                time.sleep(.05)
    result = dict(schema_version=1, status='SUCCESS' if all(r['status'] == 'SUCCESS' for r in results.values()) else 'FAILED',
                  wall_seconds=time.monotonic() - started, tasks=results,
                  cache_hits=sum(r.get('cache_hit', False) for r in results.values()),
                  release_authorized=False)
    write_json(out / 'result.json', result)
    return result
