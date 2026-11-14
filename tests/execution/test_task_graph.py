import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from simready.core.artifacts import write_json
from simready.execution.resource_pool import ResourcePool
from simready.execution.task_graph import run_task_graph
from simready.execution.task_schema import validate
from tests.fixtures.task_execution import INVENTORY, execute, task
from tests.paths import REPOSITORY_ROOT


def interval(root, name):
    rows = [
        json.loads(line)
        for line in (root / name / "stdout.log").read_text().splitlines()
    ]
    return rows[0]["start"], rows[-1]["end"]


def test_parallel_branches_and_join(tmp_path):
    tasks = [task("a", delay=0.25), task("b", delay=0.25), task("join", ["a", "b"])]
    tasks[-1]["argv"] += [
        "--input",
        "{dep:a:artifact.json}",
        "--input",
        "{dep:b:artifact.json}",
    ]
    result = execute(tmp_path, tasks)
    assert result["status"] == "SUCCESS"
    a, b, join = [interval(tmp_path / "run", x) for x in ("a", "b", "join")]
    assert max(a[0], b[0]) < min(a[1], b[1])
    assert join[0] > max(a[1], b[1])


def test_cache_hashes_and_runtime_seed_invalidation(tmp_path):
    item = task()
    (tmp_path / "source").write_text("v1")
    item["inputs"]["source"] = "source"
    item["argv"] += ["--input", "{input:source}"]
    first = execute(tmp_path, [item])
    assert execute(tmp_path, [item], "cached")["cache_hits"] == 1
    for i, (key, value) in enumerate(
        [
            ("runtime_identity", "fixture-v2"),
            ("seed", 99),
            ("quality_profile", "different"),
        ]
    ):
        item[key] = value
        assert execute(tmp_path, [item], f"changed{i}")["cache_hits"] == 0
    (tmp_path / "source").write_text("v2")
    assert execute(tmp_path, [item], "input-changed")["cache_hits"] == 0
    artifact = Path(first["tasks"]["one"]["artifact_dir"]) / "artifact.json"
    artifact.write_text("tampered")
    original = task()
    original["inputs"]["source"] = "source"
    original["argv"] += ["--input", "{input:source}"]
    (tmp_path / "source").write_text("v1")
    corrupt = execute(tmp_path, [original], "corrupt")
    assert corrupt["status"] == "FAILED" and corrupt["cache_hits"] == 0
    assert "Corrupt cache" in corrupt["tasks"]["one"]["error"]


def test_dependency_changes_invalidate_downstream(tmp_path):
    a, b = task("a"), task("b", ["a"])
    assert execute(tmp_path, [a, b])["status"] == "SUCCESS"
    a["seed"] = 99
    assert execute(tmp_path, [a, b], "changed")["cache_hits"] == 0


def test_cpu_capacity_serializes_workers(tmp_path):
    a, b = task("a", delay=0.1), task("b", delay=0.1)
    a["resources"]["cpu"] = b["resources"]["cpu"] = 4
    assert execute(tmp_path, [a, b])["status"] == "SUCCESS"
    ia, ib = interval(tmp_path / "run", "a"), interval(tmp_path / "run", "b")
    assert ia[1] < ib[0] or ib[1] < ia[0]


def test_input_mutation_is_not_cached(tmp_path):
    source = tmp_path / "source"
    source.write_text("initial")
    item = task()
    item["inputs"]["source"] = "source"
    item["argv"] = [
        "{python}",
        "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('changed'); pathlib.Path('artifact.json').write_text('output')",
        "{input:source}",
    ]
    result = execute(tmp_path, [item])
    assert result["status"] == "FAILED"
    assert "Input changed" in result["tasks"]["one"]["error"]
    assert not list((tmp_path / "cache").iterdir())


def test_escaping_output_symlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_text("outside")
    item = task()
    item["inputs"]["source"] = "source"
    item["argv"] = [
        "{python}",
        "-c",
        "import pathlib,sys; pathlib.Path('artifact.json').symlink_to(sys.argv[1])",
        "{input:source}",
    ]
    result = execute(tmp_path, [item])
    assert result["status"] == "FAILED"
    assert "escaping output" in result["tasks"]["one"]["error"]


def test_timeout_terminates_owned_descendants(tmp_path):
    marker = tmp_path / "late-marker"
    item = task()
    item["timeout_s"] = 0.15
    child_code = (
        f"import time,pathlib; time.sleep(.4); pathlib.Path({str(marker)!r}).touch()"
    )
    item["argv"] = [
        "{python}",
        "-c",
        'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(5)',
        child_code,
    ]
    assert execute(tmp_path, [item])["tasks"]["one"]["status"] == "TIMEOUT"
    time.sleep(0.5)
    assert not marker.exists()


def test_failure_skip_and_timeout_do_not_retry(tmp_path):
    a = task("a")
    a["argv"] += ["--fail"]
    slow = task("slow", delay=1)
    slow["timeout_s"] = 0.05
    result = execute(tmp_path, [a, task("b", ["a"]), slow])
    assert result["tasks"]["a"]["returncode"] == 7
    assert result["tasks"]["b"]["status"] == "SKIPPED"
    assert result["tasks"]["slow"]["status"] == "TIMEOUT"
    assert not list((tmp_path / "cache").iterdir())


@pytest.mark.parametrize(
    "mutation",
    ["cycle", "escape", "huge_ram", "unknown_vram", "missing_input", "cloud_cache"],
)
def test_invalid_plans_fail_before_work(tmp_path, mutation):
    item = task()
    inventory = copy.deepcopy(INVENTORY)
    if mutation == "cycle":
        item["deps"] = ["one"]
    if mutation == "escape":
        item["outputs"] = ["../escape"]
    if mutation == "huge_ram":
        item["resources"]["ram_gb"] = 1000
    if mutation == "unknown_vram":
        inventory["gpus"] = [
            {"uuid": "GPU-test", "vram_gb": None, "capabilities": ["cuda"]}
        ]
    if mutation == "missing_input":
        item["inputs"]["bad"] = "missing"
    if mutation == "cloud_cache":
        item["resources"]["kind"] = "cloud"
    with pytest.raises(ValueError):
        validate({"schema_version": 1, "tasks": [item]}, inventory, tmp_path)


def gpu_inventory():
    return dict(
        INVENTORY,
        gpus=[
            dict(uuid=f"GPU-test-{i}", capabilities=["cuda"], vram_gb=24)
            for i in range(2)
        ],
    )


GPU_REQUEST = {"kind": "cuda", "ram_gb": 0.1, "vram_gb": 12}


def test_physical_uuid_leases_cross_pool_and_legacy_guard(tmp_path):
    pools = [
        ResourcePool(gpu_inventory(), tmp_path / "locks", str(tmp_path / "*.gpu.lock"))
        for _ in range(2)
    ]
    first = pools[0].acquire(GPU_REQUEST)
    second = pools[1].acquire(GPU_REQUEST)
    assert first.gpu["uuid"] != second.gpu["uuid"]
    assert pools[0].acquire(GPU_REQUEST) is None
    first.close()
    second.close()
    legacy = tmp_path / "old.gpu.lock"
    legacy.touch()
    assert pools[0].acquire(GPU_REQUEST) is None
    legacy.unlink()
    recovered = pools[0].acquire(GPU_REQUEST)
    assert recovered is not None
    recovered.close()


def test_child_holds_device_until_it_exits(tmp_path):
    inventory = gpu_inventory()
    inventory["gpus"] = inventory["gpus"][:1]
    first = ResourcePool(inventory, tmp_path, None)
    second = ResourcePool(inventory, tmp_path, None)
    lease = first.acquire(GPU_REQUEST)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(.3)"], pass_fds=lease.pass_fds
    )
    try:
        lease.close()
        assert second.acquire(GPU_REQUEST) is None
        child.wait(timeout=3)
        recovered = second.acquire(GPU_REQUEST)
        assert recovered is not None
        recovered.close()
    finally:
        child.kill()
        child.wait()


def test_gpu_binding_and_queue_timeout_without_real_cuda(tmp_path):
    item = task()
    item["resources"] = GPU_REQUEST
    item["argv"] = [
        "{python}",
        "-c",
        "import os,pathlib; pathlib.Path('artifact.json').write_text(os.environ['CUDA_VISIBLE_DEVICES'])",
    ]
    inventory = gpu_inventory()
    inventory["gpus"] = inventory["gpus"][:1]
    result = run_task_graph(
        {"schema_version": 1, "tasks": [item]},
        inventory,
        tmp_path,
        tmp_path / "bound",
        tmp_path / "cache",
        tmp_path / "locks",
        legacy_glob=None,
    )
    assert result["status"] == "SUCCESS"
    assert (
        Path(result["tasks"]["one"]["artifact_dir"]) / "artifact.json"
    ).read_text() == "GPU-test-0"
    blocker = ResourcePool(inventory, tmp_path / "locks", None).acquire(GPU_REQUEST)
    try:
        item["cache"] = False
        item["queue_timeout_s"] = 0.1
        blocked = run_task_graph(
            {"schema_version": 1, "tasks": [item]},
            inventory,
            tmp_path,
            tmp_path / "blocked",
            tmp_path / "cache",
            tmp_path / "locks",
            legacy_glob=None,
        )
        assert "queue deadline" in blocked["tasks"]["one"]["error"]
    finally:
        blocker.close()


def test_rtx_requires_explicit_renderer_isolation(tmp_path):
    inventory = gpu_inventory()
    inventory["gpus"][0]["capabilities"].append("rtx")
    item = task()
    item["resources"] = dict(GPU_REQUEST, kind="rtx")
    with pytest.raises(ValueError, match="Vulkan"):
        validate({"schema_version": 1, "tasks": [item]}, inventory, tmp_path)
    inventory["gpus"][0]["rtx_isolated"] = True
    validate({"schema_version": 1, "tasks": [item]}, inventory, tmp_path)


def test_concurrent_atomic_json_writers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as executor:
        list(
            executor.map(
                lambda n: write_json(
                    tmp_path / "shared.json", {"n": n, "data": "x" * 1000}
                ),
                range(32),
            )
        )
    assert json.loads((tmp_path / "shared.json").read_text())["n"] in range(32)
    assert not list(tmp_path.glob("*.tmp"))


def test_documented_benchmark_example(tmp_path):
    root = REPOSITORY_ROOT
    subprocess.run(
        [
            sys.executable,
            str(root / "examples/scene_evaluation/prepare_fixture.py"),
            "--out",
            str(tmp_path / "input"),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready.evaluation",
            "run",
            "--protocol",
            str(tmp_path / "input/protocol.json"),
            "--candidate",
            str(tmp_path / "input/candidate.json"),
            "--out",
            str(tmp_path / "result"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    score = json.loads((tmp_path / "result/scorecard.json").read_text())
    assert score["status"] == "PASS" and score["release_authorized"] is False


def test_dag_wall_deadline_stops_worker(tmp_path):
    started = time.monotonic()
    result = execute(tmp_path, [task("slow", delay=20)], wall_timeout_s=0.2)
    assert (
        result["status"] == "FAILED" and result["tasks"]["slow"]["status"] == "TIMEOUT"
    )
    assert time.monotonic() - started < 3


def test_lease_close_is_idempotent_and_copies_resource_spec(tmp_path):
    pool = ResourcePool(INVENTORY, tmp_path / "locks", legacy_glob="")
    resources = {"kind": "cpu", "cpu": 2, "ram_gb": 1}
    lease = pool.acquire(resources)
    resources["cpu"] = 99
    lease.close()
    lease.close()
    assert pool.cpu == INVENTORY["cpu_slots"] and pool.ram == INVENTORY["ram_gb"]


def test_graph_interruption_cancels_owned_descendants_and_retains_receipts(
    tmp_path, monkeypatch
):
    from simready.execution import task_graph

    ready, late = tmp_path / "ready", tmp_path / "late"
    child = f"import pathlib,time; time.sleep(.7); pathlib.Path({str(late)!r}).touch(); time.sleep(10)"
    item = task()
    item["timeout_s"] = 3
    item["argv"] = [
        "{python}",
        "-c",
        "import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); pathlib.Path(sys.argv[2]).touch(); time.sleep(10)",
        child,
        str(ready),
    ]
    original_wait = task_graph.wait

    def interrupt_when_started(*args, **kwargs):
        if ready.exists():
            raise KeyboardInterrupt("test cancellation")
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(task_graph, "wait", interrupt_when_started)
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, [item, task("later", ["one"])])
    assert time.monotonic() - start < 2
    report = json.loads((tmp_path / "run/result.json").read_text())
    assert report["status"] == "INTERRUPTED"
    assert report["tasks"]["one"]["status"] == "CANCELLED"
    assert report["tasks"]["later"]["status"] == "CANCELLED"
    assert (
        json.loads((tmp_path / "run/one/result.json").read_text())["status"]
        == "CANCELLED"
    )
    assert not list((tmp_path / "cache").iterdir())
    time.sleep(0.8)
    assert not late.exists()


def test_submission_error_releases_acquired_lease_and_records_graph_failure(
    tmp_path, monkeypatch
):
    from simready.execution import task_graph

    leases = []
    acquire = ResourcePool.acquire

    def capture(pool, resources):
        lease = acquire(pool, resources)
        leases.append(lease)
        return lease

    def fail_submit(*args, **kwargs):
        raise RuntimeError("executor rejected submission")

    monkeypatch.setattr(ResourcePool, "acquire", capture)
    monkeypatch.setattr(task_graph.ThreadPoolExecutor, "submit", fail_submit)
    with pytest.raises(RuntimeError, match="rejected submission"):
        execute(tmp_path, [task()])
    assert leases and leases[0].closed
    assert leases[0].pool.cpu == INVENTORY["cpu_slots"]
    assert json.loads((tmp_path / "run/result.json").read_text())["status"] == "FAILED"


def test_cpu_task_cannot_inherit_a_stale_gpu_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMREADY_GPU_UUID", "GPU-parent-worker")
    item = task()
    item["argv"] = [
        "{python}",
        "-c",
        "import os,pathlib; assert 'SIMREADY_GPU_UUID' not in os.environ; assert os.environ['CUDA_VISIBLE_DEVICES']==''; pathlib.Path('artifact.json').write_text('cpu')",
    ]
    assert execute(tmp_path, [item])["status"] == "SUCCESS"
