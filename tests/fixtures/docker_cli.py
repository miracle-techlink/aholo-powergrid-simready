"""Shared synthetic inputs; no test collection or real device initialization."""

import os
import sys

import pytest


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "docker"
    script.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ["FAKE_DOCKER_ROOT"])
mode = os.environ.get("FAKE_DOCKER_MODE", "fail")
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[0] == "run":
    labels = [args[i+1] for i,v in enumerate(args) if v == "--label"]
    token = next(x.split("=",1)[1] for x in labels if x.startswith("org.simready.launch="))
    (root / "token").write_text(token)
    print("fake worker", flush=True)
    if mode == "hang": time.sleep(30)
    sys.exit(0 if mode == "success" else 7)
if args[0] == "inspect":
    if mode == "inspect-hang": time.sleep(30)
    if mode in ("inspect-fail", "known-absent"): sys.exit(1)
    token = "wrong-owner" if mode == "mismatch" else (root / "token").read_text()
    print("a" * 64 + " " + token)
    sys.exit(0)
if args[0] == "rm":
    sys.exit(9 if mode == "remove-fail" else 0)
if args[0] == "container":
    if mode != "known-absent": print("a" * 64)
    sys.exit(0)
sys.exit(99)
"""
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_DOCKER_ROOT", str(tmp_path))
    return tmp_path
