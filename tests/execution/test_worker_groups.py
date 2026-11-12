"""Real child processes validate lease inheritance, isolation and bounded overlap."""

import json
import os
import sys

import pytest

from simready.execution.worker_groups import run_worker_group


def test_children_share_owned_lease_with_bounded_overlap(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json,time,sys\n"
        "from pathlib import Path\n"
        "from simready.execution.worker_groups import gpu_worker_lease\n"
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
        jobs, directory=tmp_path / "group", max_workers=2, lock_dir=tmp_path / "locks"
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
                "from simready.execution.worker_groups import gpu_worker_lease\nwith gpu_worker_lease(): pass",
            ],
            env={**os.environ, "SIMREADY_GPU_LEASE_FD": str(stream.fileno())},
            pass_fds=(stream.fileno(),),
            capture_output=True,
            timeout=10,
        )
    assert result.returncode != 0
    assert fake.exists()


def test_failed_child_releases_only_its_group_lock(tmp_path):
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
        lock_dir=tmp_path / "locks",
    )
    assert report["status"] == "FAILED"
    assert not (tmp_path / "locks/simready_runtime.gpu.lock").exists()
