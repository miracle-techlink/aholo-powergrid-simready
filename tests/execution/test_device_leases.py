"""One physical-UUID lock across DAG, launchers and groups; CPU subprocesses only."""

import json
import sys

import pytest

from simready.execution.processes.local import run_process
from simready.execution.resources.resource_pool import ResourcePool
from simready.execution.tasks.graph import run_task_graph
from simready.execution.workers.device_lease import device_lease
from simready.execution.workers.device_recovery import recover_device
from simready.execution.workers.isaac_launcher import launch_worker
from simready.execution.workers.worker_groups import run_worker_group
from tests.fixtures.docker_cli import fake_docker as fake_docker
from tests.fixtures.task_execution import task


def paths(tmp_path):
    repo, assets = tmp_path / "repo", tmp_path / "assets"
    repo.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    return dict(repo=repo, assets=assets, lock_dir=tmp_path / "locks")


def test_launcher_and_group_contend_with_the_same_dag_pool(tmp_path, device_inventory):
    locations = paths(tmp_path)
    pool = ResourcePool(device_inventory, locations["lock_dir"], None)
    lease = pool.acquire(dict(kind="cuda", ram_gb=0.1, vram_gb=1))
    try:
        with pytest.raises(RuntimeError, match="RESOURCE_BUSY"):
            launch_worker(
                ["-c", "raise AssertionError('must not run')"],
                backend="local",
                output=tmp_path / "blocked",
                device_inventory=device_inventory,
                **locations,
            )
        assert (
            json.loads((tmp_path / "blocked/launch.json").read_text())["status"]
            == "FAILED"
        )
        with pytest.raises(RuntimeError, match="RESOURCE_BUSY"):
            run_worker_group(
                [{"name": "blocked"}],
                directory=tmp_path / "group",
                lock_dir=locations["lock_dir"],
                device_inventory=device_inventory,
            )
    finally:
        lease.close()
    assert (
        launch_worker(
            ["-c", "pass"],
            backend="local",
            output=tmp_path / "ready",
            device_inventory=device_inventory,
            **locations,
        )
        == 0
    )
    assert (
        json.loads((tmp_path / "ready/launch.json").read_text())["device_lease"][
            "gpu_uuid"
        ]
        == "GPU-fixture-0"
    )


def test_dag_lease_delegates_through_plain_wrapper_into_nested_launcher(
    tmp_path, device_inventory
):
    locations = paths(tmp_path)
    child = tmp_path / "child.py"
    child.write_text(
        "import os\nfrom pathlib import Path\nfrom simready.execution.workers.isaac_launcher import launch_worker\n"
        "out=Path(os.environ['SIMREADY_OUT'])\n"
        f"launch_worker(['-c',\"from simready.execution.workers.worker_groups import gpu_worker_lease; import os; assert os.environ['SIMREADY_GPU_UUID']=='GPU-fixture-0';\\nwith gpu_worker_lease(): pass\"],repo={str(locations['repo'])!r},assets={str(locations['assets'])!r},output=out/'nested',backend='local',resource_kind='cuda')\n"
        "(out/'artifact.json').write_text('ok')\n"
    )
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import os,sys\nfrom pathlib import Path\nfrom simready.execution.processes.local import run_process\n"
        "root=Path(os.environ['SIMREADY_OUT'])\n"
        f"result=run_process([sys.executable,{str(child)!r}],cwd=root,timeout=5,stdout=root/'child.stdout',stderr=root/'child.stderr',env={{'SIMREADY_OUT':str(root)}})\n"
        "raise SystemExit(result.returncode)\n"
    )
    item = task()
    item.update(
        argv=["{python}", "{input:worker}"],
        inputs={"worker": str(wrapper), "child": str(child)},
        cache=False,
        resources=dict(kind="cuda", ram_gb=0.1, vram_gb=1),
        timeout_s=10,
    )
    result = run_task_graph(
        {"schema_version": 1, "tasks": [item]},
        device_inventory,
        tmp_path,
        tmp_path / "dag",
        tmp_path / "cache",
        locations["lock_dir"],
        legacy_glob=None,
    )
    assert result["status"] == "SUCCESS", result
    assert (tmp_path / "dag/one/files/artifact.json").read_text() == "ok"
    pool = ResourcePool(device_inventory, locations["lock_dir"], None)
    recovered = pool.acquire(dict(kind="cuda", ram_gb=0.1, vram_gb=1))
    assert recovered is not None
    recovered.close()


def test_cpu_launcher_never_discovers_devices_and_gpu_discovery_failure_is_terminal(
    tmp_path, monkeypatch
):
    locations = paths(tmp_path)

    def unavailable():
        raise RuntimeError("GPU discovery failed")

    monkeypatch.setattr(
        "simready.execution.workers.device_lease.discover_inventory", unavailable
    )
    assert (
        launch_worker(
            ["-c", "pass"],
            backend="local",
            resource_kind="cpu",
            output=tmp_path / "cpu",
            **locations,
        )
        == 0
    )
    with pytest.raises(RuntimeError, match="discovery"):
        launch_worker(
            ["-c", "pass"], backend="local", output=tmp_path / "gpu", **locations
        )
    assert json.loads((tmp_path / "gpu/launch.json").read_text())["status"] == "FAILED"


def test_nested_quarantine_is_visible_to_owner_and_blocks_later_acquisition(
    tmp_path, device_inventory
):
    with device_lease(
        resource_kind="cuda",
        device_inventory=device_inventory,
        lock_dir=tmp_path / "locks",
    ) as lease:
        code = "from simready.execution.workers.device_lease import device_lease\nwith device_lease(resource_kind='cuda') as borrowed: borrowed.quarantine({'fixture':'unknown child'})"
        result = run_process(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            timeout=5,
            stdout=tmp_path / "stdout",
            stderr=tmp_path / "stderr",
            env=lease.environment,
            pass_fds=lease.pass_fds,
        )
        assert result.returncode == 0 and lease.quarantined
    with pytest.raises(RuntimeError, match="quarantined"):
        with device_lease(
            resource_kind="cuda",
            device_inventory=device_inventory,
            lock_dir=tmp_path / "locks",
        ):
            pytest.fail("quarantine was bypassed")


@pytest.mark.parametrize(
    "recovery_mode,expected",
    [
        ("fail", "RECOVERED"),
        ("inspect-fail", "NEEDS_REVIEW"),
        ("mismatch", "NEEDS_REVIEW"),
    ],
)
def test_uncertain_docker_cleanup_quarantines_until_token_verified_recovery(
    tmp_path, device_inventory, fake_docker, monkeypatch, recovery_mode, expected
):
    locations = paths(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_MODE", "inspect-fail")
    assert (
        launch_worker(
            ["-c", "pass"],
            output=tmp_path / "run",
            device_inventory=device_inventory,
            **locations,
        )
        == 7
    )
    lock = locations["lock_dir"] / "GPU-fixture-0.lease"
    original = lock.read_text()
    assert json.loads(original)["status"] == "QUARANTINED"
    monkeypatch.setenv("FAKE_DOCKER_MODE", recovery_mode)
    result = recover_device(
        "GPU-fixture-0",
        directory=tmp_path / "recovery",
        lock_dir=locations["lock_dir"],
        timeout_s=3,
    )
    assert result["status"] == expected
    assert lock.read_text() == ("" if expected == "RECOVERED" else original)


def test_recovery_does_not_touch_a_live_lease(tmp_path, device_inventory):
    with device_lease(
        resource_kind="cuda",
        device_inventory=device_inventory,
        lock_dir=tmp_path / "locks",
    ) as lease:
        lease.quarantine({"fixture": "still owned"})
        result = recover_device(
            "GPU-fixture-0",
            directory=tmp_path / "recovery",
            lock_dir=tmp_path / "locks",
        )
        assert result["status"] == "BUSY" and lease.quarantined


def test_recovery_can_confirm_absence_of_previously_verified_full_id(
    tmp_path, device_inventory, fake_docker, monkeypatch
):
    locations = paths(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_MODE", "remove-fail")
    assert (
        launch_worker(
            ["-c", "pass"],
            output=tmp_path / "run",
            device_inventory=device_inventory,
            **locations,
        )
        == 7
    )
    monkeypatch.setenv("FAKE_DOCKER_MODE", "known-absent")
    result = recover_device(
        "GPU-fixture-0", directory=tmp_path / "recovery", lock_dir=locations["lock_dir"]
    )
    assert result["status"] == "RECOVERED" and result["known_id_absent"] is True
    assert not (locations["lock_dir"] / "GPU-fixture-0.lease").read_text()


def test_cpu_docker_launch_omits_gpu_and_never_discovers_devices(
    tmp_path, fake_docker, monkeypatch
):
    def forbidden():
        raise AssertionError("CPU launch must not discover GPU devices")

    monkeypatch.setattr(
        "simready.execution.workers.device_lease.discover_inventory", forbidden
    )
    monkeypatch.setenv("FAKE_DOCKER_MODE", "success")
    assert (
        launch_worker(
            ["-c", "pass"],
            resource_kind="cpu",
            output=tmp_path / "run",
            **paths(tmp_path),
        )
        == 0
    )
    commands = [
        json.loads(line)
        for line in (fake_docker / "calls.jsonl").read_text().splitlines()
    ]
    assert "--gpus" not in commands[0]


def test_cleanup_budget_cannot_exceed_nested_grace_allowance(tmp_path):
    with pytest.raises(ValueError, match="<=15"):
        launch_worker(
            ["-c", "pass"],
            backend="local",
            resource_kind="cpu",
            output=tmp_path / "run",
            cleanup_timeout_s=16,
            **paths(tmp_path),
        )
    assert not (tmp_path / "run").exists()


def test_mig_alias_requires_parent_uuid_and_contends_with_physical_device(
    tmp_path, device_inventory
):
    parent = ResourcePool(device_inventory, tmp_path / "locks", None)
    resources = dict(kind="cuda", ram_gb=0.1, vram_gb=1)
    lease = parent.acquire(resources)
    alias_inventory = {
        **device_inventory,
        "gpus": [{**device_inventory["gpus"][0], "uuid": "MIG-fixture"}],
    }
    try:
        with pytest.raises(ValueError, match="physical_uuid"):
            ResourcePool(alias_inventory, tmp_path / "locks", None).acquire(resources)
        alias_inventory["gpus"][0]["physical_uuid"] = "GPU-fixture-0"
        assert (
            ResourcePool(alias_inventory, tmp_path / "locks", None).acquire(resources)
            is None
        )
    finally:
        lease.close()
    recovered = ResourcePool(alias_inventory, tmp_path / "locks", None).acquire(
        resources
    )
    assert recovered is not None
    assert recovered.environment["CUDA_VISIBLE_DEVICES"] == "MIG-fixture"
    recovered.close()


@pytest.mark.parametrize(
    "memory,capacity,known",
    [("32768", 32, True), ("[N/A]", 0, False), ("N/A", 0, False)],
)
def test_discovery_preserves_uuid_when_memory_capacity_is_unknown(
    tmp_path, monkeypatch, memory, capacity, known
):
    from types import SimpleNamespace

    from simready.execution.workers.device_lease import discover_inventory

    def query(*args, **kwargs):
        kwargs["stdout"].write_text(f"0, GPU-unified-memory, {memory}\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("simready.execution.workers.device_lease.run_process", query)
    inventory = discover_inventory()
    device = inventory["gpus"][0]
    assert device["uuid"] == "GPU-unified-memory"
    assert device["vram_gb"] == capacity and device["vram_capacity_known"] is known
    pool = ResourcePool(inventory, tmp_path / "locks", None)
    if not known:
        assert pool.acquire(dict(kind="cuda", ram_gb=0.1, vram_gb=1)) is None
    lease = pool.acquire(dict(kind="cuda", ram_gb=0.1, vram_gb=0))
    assert lease is not None
    other = ResourcePool(inventory, tmp_path / "locks", None)
    try:
        assert other.acquire(dict(kind="cuda", ram_gb=0.1, vram_gb=0)) is None
    finally:
        lease.close()


@pytest.mark.parametrize("memory", ["nan", "inf", "-1", "invalid"])
def test_discovery_rejects_corrupt_capacity(monkeypatch, memory):
    from types import SimpleNamespace

    from simready.execution.workers.device_lease import discover_inventory

    def query(*args, **kwargs):
        kwargs["stdout"].write_text(f"0, GPU-fixture, {memory}\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("simready.execution.workers.device_lease.run_process", query)
    with pytest.raises(RuntimeError, match="Invalid GPU memory"):
        discover_inventory()
