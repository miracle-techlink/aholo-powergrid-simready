"""Real CPU processes and a fake Docker CLI; no daemon, GPU or remote host."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import pytest

from simready.execution.isaac_launcher import launch_worker, main, seed_runtime_cache
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from tests.fixtures.docker_cli import fake_docker as fake_docker


@pytest.fixture
def launch_paths(tmp_path, device_inventory):
    repo, assets, output = (tmp_path / name for name in ("repo", "assets", "out"))
    repo.mkdir()
    assets.mkdir()
    return {
        "repo": repo,
        "assets": assets,
        "output": output,
        "device_inventory": device_inventory,
        "lock_dir": tmp_path / "locks",
    }


def receipt(paths):
    return json.loads((paths["output"] / "launch.json").read_text())


def test_local_exit_and_retry_keep_separate_logs(launch_paths):
    for text, code in (("first", 0), ("second", 7)):
        assert (
            launch_worker(
                [
                    "-c",
                    f"import sys; print('{text}'); print('err',file=sys.stderr); sys.exit({code})",
                ],
                backend="local",
                resource_kind="cpu",
                timeout_s=5,
                **launch_paths,
            )
            == code
        )
    reports = [
        json.loads(path.read_text())
        for path in launch_paths["output"].glob("launch*.json")
    ]
    assert {row["status"] for row in reports} == {"COMPLETE", "FAILED"}
    assert {Path(row["stdout"]).read_text() for row in reports} == {
        "first\n",
        "second\n",
    }
    assert all(Path(row["stderr"]).read_text() == "err\n" for row in reports)


def _running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("cancel", [False, True])
def test_local_deadline_or_cancellation_reaps_owned_group(launch_paths, cancel):
    pidfile = launch_paths["repo"] / "child.pid"
    code = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    event = Event()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    def cancel_after_start():
        deadline = time.monotonic() + 4
        while not pidfile.exists() and time.monotonic() < deadline:
            event.wait(0.01)
        event.set()

    thread = Thread(target=cancel_after_start) if cancel else None
    if thread is not None:
        thread.start()
    try:
        with pytest.raises(ProcessCancelledError if cancel else ProcessTimeoutError):
            launch_worker(
                ["-c", code],
                backend="local",
                resource_kind="cpu",
                timeout_s=5 if cancel else 0.5,
                cancel_event=event if cancel else None,
                **launch_paths,
            )
        assert receipt(launch_paths)["status"] == ("CANCELLED" if cancel else "TIMEOUT")
        assert unrelated.poll() is None
        assert pidfile.is_file()
        deadline = time.monotonic() + 2
        while _running(int(pidfile.read_text())) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _running(int(pidfile.read_text()))
    finally:
        unrelated.kill()
        unrelated.wait()
        if thread is not None:
            thread.join(timeout=5)
        if pidfile.exists() and _running(int(pidfile.read_text())):
            os.kill(int(pidfile.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, None])
@pytest.mark.parametrize("option", ["timeout_s", "cleanup_timeout_s"])
def test_invalid_deadline_rejected_before_output(launch_paths, value, option):
    with pytest.raises(ValueError):
        launch_worker(
            ["-c", "pass"], backend="local", **{option: value}, **launch_paths
        )
    assert not launch_paths["output"].exists()


def _calls(root):
    path = root / "calls.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


@pytest.mark.parametrize(
    "mode,cleanup",
    [
        ("success", "NOT_REQUIRED_ON_SUCCESS"),
        ("fail", "REMOVED"),
        ("mismatch", "OWNERSHIP_MISMATCH"),
        ("inspect-fail", "UNKNOWN"),
        ("remove-fail", "FAILED"),
        ("inspect-hang", "FAILED"),
    ],
)
def test_docker_cleanup_requires_matching_token_and_preserves_exit(
    launch_paths,
    fake_docker,
    monkeypatch,
    mode,
    cleanup,
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
    started = time.monotonic()
    code = launch_worker(
        ["-c", "pass"], timeout_s=5, cleanup_timeout_s=0.3, **launch_paths
    )
    assert code == (0 if mode == "success" else 7)
    assert time.monotonic() - started < 3
    report = receipt(launch_paths)
    assert report["container"]["cleanup"]["status"] == cleanup
    removed = [args for args in _calls(fake_docker) if args[0] == "rm"]
    assert removed == (
        [["rm", "--force", "a" * 64]] if mode in ("fail", "remove-fail") else []
    )
    if mode == "inspect-hang":
        assert report["container"]["cleanup"]["error"] == "ProcessTimeoutError"


@pytest.mark.parametrize("cancel", [False, True])
def test_docker_deadline_or_cancel_cleans_owned_id(
    launch_paths, fake_docker, monkeypatch, cancel
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "hang")
    event = Event()

    def stop_when_started():
        deadline = time.monotonic() + 3
        while not (fake_docker / "token").exists() and time.monotonic() < deadline:
            event.wait(0.01)
        event.set()

    thread = Thread(target=stop_when_started) if cancel else None
    if thread is not None:
        thread.start()
    try:
        with pytest.raises(ProcessCancelledError if cancel else ProcessTimeoutError):
            launch_worker(
                ["-c", "pass"],
                timeout_s=5 if cancel else 0.3,
                cleanup_timeout_s=2,
                cancel_event=event if cancel else None,
                **launch_paths,
            )
    finally:
        if thread is not None:
            thread.join(timeout=4)
    report = receipt(launch_paths)
    assert report["status"] == ("CANCELLED" if cancel else "TIMEOUT")
    assert report["container"]["cleanup"]["status"] == "REMOVED"
    assert _calls(fake_docker)[-1] == ["rm", "--force", "a" * 64]


@pytest.mark.parametrize("backend", ["local", "docker"])
def test_precancel_does_not_start_worker_or_cleanup(launch_paths, fake_docker, backend):
    event = Event()
    event.set()
    with pytest.raises(ProcessCancelledError):
        launch_worker(
            ["-c", "raise AssertionError('must not run')"],
            backend=backend,
            resource_kind="cpu" if backend == "local" else "rtx",
            cancel_event=event,
            **launch_paths,
        )
    report = receipt(launch_paths)
    assert report["status"] == "CANCELLED"
    assert _calls(fake_docker) == []
    if backend == "docker":
        assert report["container"]["cleanup"]["status"] == "NOT_STARTED"


def test_interrupt_cleanup_failure_preserves_original_error(
    launch_paths, fake_docker, monkeypatch
):
    from simready.execution import isaac_container

    process = isaac_container.run_process

    def interrupt_after_run(command, **kwargs):
        if command[1] == "run":
            process(command, **kwargs)
            raise KeyboardInterrupt("original interrupt")
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(isaac_container, "run_process", interrupt_after_run)
    with pytest.raises(KeyboardInterrupt, match="original interrupt"):
        launch_worker(["-c", "pass"], timeout_s=5, **launch_paths)
    report = receipt(launch_paths)
    assert report["status"] == "INTERRUPTED"
    assert report["container"]["cleanup"]["error"] == "OSError"


def test_worker_name_and_label_do_not_replace_docker_ownership(
    launch_paths, fake_docker
):
    worker = [
        "-m",
        "fixture.worker",
        "--name",
        "cup",
        "--label",
        "org.simready.launch=" + "b" * 32,
    ]
    assert launch_worker(worker, timeout_s=5, **launch_paths) == 7
    report = receipt(launch_paths)
    assert report["container"]["cleanup"]["status"] == "REMOVED"
    assert report["container"]["ownership"] != "b" * 32
    assert _calls(fake_docker)[0][-len(worker) :] == worker
    assert _calls(fake_docker)[-1] == ["rm", "--force", "a" * 64]


def test_ssh_local_timeout_records_remote_unknown(tmp_path, monkeypatch):
    from simready.execution import isaac_container

    remote_argv = []

    def fake_remote(host, command):
        remote_argv.extend(command)
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    monkeypatch.setattr(isaac_container, "remote_command", fake_remote)
    local = tmp_path / "local"
    with pytest.raises(ProcessTimeoutError):
        main(
            [
                "--backend",
                "ssh",
                "--ssh-host",
                "fixture",
                "--repo",
                "/remote/repo",
                "--assets",
                "/remote/assets",
                "--out",
                "/remote/out",
                "--timeout-s",
                "0.2",
                "--cleanup-timeout-s",
                "0.1",
                "--ssh-local-output",
                str(local),
                "--accept-eula",
            ]
        )
    report = json.loads((local / "launch.json").read_text())
    assert report["status"] == "TIMEOUT"
    assert report["remote_status"] == "UNKNOWN"
    assert report["remote_cancellation_confirmed"] is False
    assert remote_argv[remote_argv.index("--timeout-s") + 1] == "0.2"
    assert remote_argv[remote_argv.index("--cleanup-timeout-s") + 1] == "0.1"
    assert remote_argv[remote_argv.index("--out") + 1] == "/remote/out"


def test_ssh_dryrun_propagates_deadlines_without_local_output(tmp_path, capsys):
    local = tmp_path / "local"
    assert (
        main(
            [
                "--backend",
                "ssh",
                "--ssh-host",
                "fixture",
                "--repo",
                "/remote/repo",
                "--assets",
                "/remote/assets",
                "--out",
                "/remote/out",
                "--dry-run",
                "--ssh-local-output",
                str(local),
                "--timeout-s",
                "31",
            ]
        )
        == 0
    )
    command = shlex.split(capsys.readouterr().out.strip())
    assert "--timeout-s 31.0" in command[-1]
    assert not local.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "dangling", "second-tree"])
def test_cache_source_links_rejected_before_any_publish(tmp_path, kind):
    source, output = tmp_path / "source", tmp_path / "output"
    cache = source / ".isaac-cache"
    cache.mkdir(parents=True)
    (cache / "shader").write_text("valid")
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "second-tree":
        (source / ".isaac-home").mkdir()
        (source / ".isaac-home/.cache").symlink_to(outside, target_is_directory=True)
    elif kind == "directory":
        (cache / "link").symlink_to(outside, target_is_directory=True)
    else:
        file = outside / "file"
        if kind == "file":
            file.write_text("private")
        (cache / "link").symlink_to(file)
    with pytest.raises(ValueError, match="symlinks"):
        seed_runtime_cache(source, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "relation", ["under-first", "under-second", "cross-cache", "normalized"]
)
def test_cache_overlap_rejected_without_partial_copy(tmp_path, relation):
    source = tmp_path / "source"
    if relation == "cross-cache":
        source = tmp_path / "outer/.isaac-cache/source"
    (source / ".isaac-cache").mkdir(parents=True)
    (source / ".isaac-cache/shader").write_text("original")
    (source / ".isaac-home/.cache").mkdir(parents=True)
    output = {
        "under-first": source / ".isaac-cache/run",
        "under-second": source / ".isaac-home/.cache/run",
        "cross-cache": tmp_path / "outer",
        "normalized": source / ".isaac-cache/unused/../run",
    }[relation]
    with pytest.raises(ValueError, match="overlap"):
        seed_runtime_cache(source, output)
    assert not list(source.rglob(".cache-seed-*"))
    assert (source / ".isaac-cache/shader").read_text() == "original"
    if relation != "cross-cache":
        assert not output.exists()


def test_cache_copy_failure_does_not_publish_first_tree(tmp_path, monkeypatch):
    from simready.execution import isaac_launcher

    source, output = tmp_path / "source", tmp_path / "output"
    for root in (source / ".isaac-cache", source / ".isaac-home/.cache"):
        root.mkdir(parents=True)
        (root / "shader").write_text("new")
    (output / ".isaac-cache").mkdir(parents=True)
    (output / ".isaac-cache/shader").write_text("existing")
    copy = isaac_launcher.shutil.copytree

    def fail_second(origin, *args, **kwargs):
        if Path(origin) == source / ".isaac-home/.cache":
            raise OSError("fixture disk failure")
        return copy(origin, *args, **kwargs)

    monkeypatch.setattr(isaac_launcher.shutil, "copytree", fail_second)
    with pytest.raises(OSError, match="fixture disk failure"):
        seed_runtime_cache(source, output)
    assert (output / ".isaac-cache/shader").read_text() == "existing"
    assert not (output / ".isaac-home/.cache").exists()
    assert not list(output.glob(".cache-seed-*"))


def test_cache_cleanup_failure_preserves_copy_interrupt(tmp_path, monkeypatch):
    from simready.execution import isaac_launcher

    source, output = tmp_path / "source", tmp_path / "output"
    (source / ".isaac-cache").mkdir(parents=True)

    def fail_copy(*args, **kwargs):
        raise KeyboardInterrupt("original copy interrupt")

    def fail_cleanup(*args, **kwargs):
        raise OSError("fixture cleanup error")

    monkeypatch.setattr(isaac_launcher.shutil, "copytree", fail_copy)
    monkeypatch.setattr(isaac_launcher.shutil, "rmtree", fail_cleanup)
    with pytest.raises(KeyboardInterrupt, match="original copy interrupt"):
        seed_runtime_cache(source, output)
    assert not (output / ".isaac-cache").exists()
    assert len(list(output.glob(".cache-seed-*"))) == 1


def test_cache_cleanup_error_is_reported_when_copy_succeeded(tmp_path, monkeypatch):
    from simready.execution import isaac_launcher

    source, output = tmp_path / "source", tmp_path / "output"
    (source / ".isaac-cache").mkdir(parents=True)
    (source / ".isaac-cache/shader").write_text("valid")

    def fail_cleanup(*args, **kwargs):
        raise OSError("fixture cleanup error")

    monkeypatch.setattr(isaac_launcher.shutil, "rmtree", fail_cleanup)
    with pytest.raises(OSError, match="fixture cleanup error"):
        seed_runtime_cache(source, output)
    assert (output / ".isaac-cache/shader").read_text() == "valid"
