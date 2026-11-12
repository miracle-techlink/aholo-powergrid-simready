"""Cooperative, exclusive device leases shared by processes on one Linux host."""
import fcntl
import glob
from pathlib import Path
import threading


class Lease:
    def __init__(self, pool, resources, gpu=None, handle=None):
        self.pool, self.resources, self.gpu, self.handle = pool, resources, gpu, handle

    @property
    def pass_fds(self):
        # Child retains the flock if the scheduler exits unexpectedly. We close
        # our descriptor without LOCK_UN, which would unlock the child's copy.
        return (self.handle.fileno(),) if self.handle else ()

    def close(self):
        with self.pool.guard:
            self.pool.cpu += self.resources.get('cpu', 1)
            self.pool.ram += self.resources['ram_gb']
            if self.resources['kind'] == 'cloud':
                self.pool.cloud += 1
            if self.handle:
                self.handle.close()
                self.pool.busy.remove(self.gpu['uuid'])


class ResourcePool:
    def __init__(self, inventory, lock_dir, legacy_glob='/tmp/*.gpu.lock'):
        self.cpu = inventory['cpu_slots']
        self.ram = inventory['ram_gb']
        self.cloud = inventory.get('cloud_slots', 0)
        self.gpus = inventory.get('gpus', [])
        self.busy = set()
        self.guard = threading.Lock()
        self.directory = Path(lock_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.legacy_glob = legacy_glob

    def acquire(self, resources):
        with self.guard:
            if resources.get('cpu', 1) > self.cpu or resources['ram_gb'] > self.ram:
                return None
            kind = resources['kind']
            if kind == 'cloud' and self.cloud < 1:
                return None
            handle = gpu = None
            if kind in ('cuda', 'rtx'):
                if self.legacy_glob and glob.glob(self.legacy_glob):
                    return None
                for candidate in self.gpus:
                    if (kind not in candidate['capabilities'] or candidate['uuid'] in self.busy
                            or candidate['vram_gb'] < resources['vram_gb']):
                        continue
                    attempt = (self.directory / f'{candidate["uuid"]}.lease').open('a+')
                    try:
                        fcntl.flock(attempt.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        attempt.close()
                        continue
                    handle, gpu = attempt, candidate
                    self.busy.add(gpu['uuid'])
                    break
                if handle is None:
                    return None
            self.cpu -= resources.get('cpu', 1)
            self.ram -= resources['ram_gb']
            if kind == 'cloud':
                self.cloud -= 1
            return Lease(self, resources, gpu, handle)
