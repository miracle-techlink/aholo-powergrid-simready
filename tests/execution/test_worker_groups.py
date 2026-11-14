"""Real child processes validate lease inheritance, isolation and bounded overlap."""

import json
import os
import sys
import time

import pytest

from simready.execution.workers.worker_groups import run_worker_group


def test_children_share_owned_lease_with_bounded_overlap(tmp_path, device_inventory):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json,time,sys\n"
        "from pathlib import Path\n"
        "from simready.execution.workers.worker_groups import gpu_worker_lease\n"
        "with gpu_worker_lease() as lock:\n"
        "    start=time.time()\n"
        "    time.sleep(.2)\n"
        "    assert lock.exists()\n"
        "    Path(sys.argv[1]).write_text(json.dumps([start,time.time()]))\n"
    )
    jobs = [
        {
            "name": f"worker_{i}",
            "argv": [sys.executable, str(script), str(tmp_path / f"span{i}.json")],
            "cwd": tmp_path,
            "timeout_s": 10,
            "token_mode": "no_llm",
        }
        for i in range(3)
    ]
    report = run_worker_group(
        jobs,
        directory=tmp_path / "group",
        max_workers=2,
        lock_dir=tmp_path / "locks",
        device_inventory=device_inventory,
    )
    assert report["status"] == "SUCCESS"
    spans = [json.loads((tmp_path / f"span{i}.json").read_text()) for i in range(3)]
    events = sorted(
        [(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans]
    )
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    assert peak == 2
    assert not list((tmp_path / "locks").glob("*.gpu.lock"))
    assert report["summary"]["usage_complete"]


def test_foreign_lock_is_not_removed_or_bypassed(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir()
    foreign = locks / "other.gpu.lock"
    foreign.write_text("another terminal's job")
    with pytest.raises(RuntimeError, match="RESOURCE_BUSY"):
        run_worker_group(
            [{"name": "worker"}], directory=tmp_path / "group", lock_dir=locks
        )
    assert foreign.read_text() == "another terminal's job"


def test_plain_file_or_environment_cannot_bypass_exclusive_lease(tmp_path):
    import subprocess

    fake = tmp_path / "ticket"
    fake.write_text("{}")
    with fake.open("rb") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from simready.execution.workers.worker_groups import gpu_worker_lease\nwith gpu_worker_lease(): pass",
            ],
            env={**os.environ, "SIMREADY_GPU_LEASE_FD": str(stream.fileno())},
            pass_fds=(stream.fileno(),),
            capture_output=True,
            timeout=10,
        )
    assert result.returncode != 0
    assert fake.exists()


def test_failed_child_releases_only_its_group_lock(tmp_path, device_inventory):
    report = run_worker_group(
        [
            {
                "name": "failed",
                "argv": [sys.executable, "-c", "raise SystemExit(2)"],
                "cwd": tmp_path,
                "timeout_s": 10,
            }
        ],
        directory=tmp_path / "group",
        device_inventory=device_inventory,
        lock_dir=tmp_path / "locks",
    )
    assert report["status"] == "FAILED"
    assert not (tmp_path / "locks/simready_runtime.gpu.lock").exists()


def test_interrupted_group_cancels_owned_workers_before_releasing_lease(
    tmp_path, monkeypatch, device_inventory
):
    from simready.execution.workers import worker_groups

    ready, late = tmp_path / "ready", tmp_path / "late"
    profile = worker_groups.profile_command

    def interrupt_after_worker_start(*args, **kwargs):
        if kwargs["name"] == "interruptor":
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            raise KeyboardInterrupt("fixture group interruption")
        return profile(*args, **kwargs)

    monkeypatch.setattr(worker_groups, "profile_command", interrupt_after_worker_start)
    code = f"import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(.7); pathlib.Path({str(late)!r}).touch(); time.sleep(10)"
    jobs = [
        {
            "name": name,
            "argv": [sys.executable, "-c", code],
            "cwd": tmp_path,
            "timeout_s": 3,
        }
        for name in ["interruptor", "running"]
    ]
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_worker_group(
            jobs,
            directory=tmp_path / "group",
            device_inventory=device_inventory,
            max_workers=2,
            lock_dir=tmp_path / "locks",
        )
    assert time.monotonic() - start < 2
    report = json.loads((tmp_path / "group/group.json").read_text())
    assert report["status"] == "INTERRUPTED"
    assert report["summary"]["stage_count"] == 2
    assert set(report["summary"]["failed_stages"]) == {"interruptor", "running"}
    assert (
        json.loads((tmp_path / "group/running/profile.json").read_text())["status"]
        == "CANCELLED"
    )
    assert not (tmp_path / "locks/simready_runtime.gpu.lock").exists()
    time.sleep(0.8)
    assert not late.exists()
