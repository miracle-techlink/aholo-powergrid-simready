"""Owned nested sessions must unwind on cancellation and owner death, on CPU."""

import json
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import pytest

from simready.execution.processes.local import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)


def nested_script(root):
    script = root / "nested.py"
    script.write_text(
        "import os,sys,time,json\n"
        "from pathlib import Path\n"
        "from simready.execution.workers.profiling import profile_command\n"
        "root=Path(sys.argv[1]); depth=int(sys.argv[2])\n"
        "if depth == 0:\n"
        "    (root/'leaf.pid').write_text(str(os.getpid()))\n"
        "    time.sleep(5)\n"
        "    (root/'escaped').touch()\n"
        "else:\n"
        "    profile_command([sys.executable,__file__,str(root),str(depth-1)],name=str(depth),directory=root/str(depth),cwd=root,timeout_s=20,env={})\n"
    )
    return script


def wait_file(path, seconds=5):
    deadline = time.monotonic() + seconds
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.is_file()


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("cancel", [False, True])
def test_nested_sessions_cancel_and_write_terminal_profiles(tmp_path, cancel):
    script = nested_script(tmp_path)
    event = Event()

    def stop():
        wait_file(tmp_path / "leaf.pid")
        event.set()

    thread = Thread(target=stop) if cancel else None
    if thread:
        thread.start()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    started = time.monotonic()
    try:
        with pytest.raises(ProcessCancelledError if cancel else ProcessTimeoutError):
            run_process(
                [sys.executable, str(script), str(tmp_path), "2"],
                cwd=tmp_path,
                timeout=10 if cancel else 1,
                stdout=tmp_path / "stdout",
                stderr=tmp_path / "stderr",
                cancel_event=event if cancel else None,
            )
        assert time.monotonic() - started < 3
        for depth in (1, 2):
            assert (
                json.loads((tmp_path / str(depth) / "profile.json").read_text())[
                    "status"
                ]
                == "CANCELLED"
            )
        assert not alive(int((tmp_path / "leaf.pid").read_text()))
        assert unrelated.poll() is None and not (tmp_path / "escaped").exists()
    finally:
        unrelated.kill()
        unrelated.wait()
        if thread:
            thread.join(timeout=5)


def test_owner_death_cascades_eof_to_nested_sessions(tmp_path):
    script = nested_script(tmp_path)
    owner_code = (
        "from simready.execution.processes.local import run_process; import sys; "
        f"run_process([sys.executable,{str(script)!r},{str(tmp_path)!r},'2'],cwd={str(tmp_path)!r},timeout=20,stdout={str(tmp_path / 'stdout')!r},stderr={str(tmp_path / 'stderr')!r})"
    )
    owner = subprocess.Popen([sys.executable, "-c", owner_code])
    try:
        wait_file(tmp_path / "leaf.pid")
        owner.kill()
        owner.wait(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            path = tmp_path / "2/profile.json"
            if path.is_file() and json.loads(path.read_text())["status"] == "CANCELLED":
                break
            time.sleep(0.02)
        assert (
            json.loads((tmp_path / "2/profile.json").read_text())["status"]
            == "CANCELLED"
        )
        assert not alive(int((tmp_path / "leaf.pid").read_text()))
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()


def test_acknowledged_but_unresponsive_owner_has_bounded_grace(tmp_path):
    code = "from simready.execution.processes.cancellation import parent_cancellation; import time; parent_cancellation(); time.sleep(20)"
    started = time.monotonic()
    with pytest.raises(ProcessTimeoutError):
        run_process(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            timeout=0.3,
            cancellation_grace_s=0.15,
            stdout=tmp_path / "stdout",
            stderr=tmp_path / "stderr",
        )
    assert time.monotonic() - started < 1.5
