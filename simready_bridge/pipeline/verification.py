"""Verifier process boundary and immutable evidence references."""

import sys
from pathlib import Path

from ..artifacts import digest, read_json
from ..process import run_process


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path)}


def verify(protocol, candidate, directory, *, protocol_hash, timeout):
    directory = Path(directory).resolve()
    command = [
        sys.executable,
        "-m",
        "simready_bridge.benchmark",
        "worker",
        "--protocol",
        str(protocol),
        "--candidate",
        str(candidate),
        "--out",
        str(directory),
        "--expected-protocol",
        protocol_hash,
        "--expected-candidate",
        digest(candidate),
    ]
    result = run_process(
        command,
        cwd=Path(__file__).resolve().parents[2],
        timeout=timeout,
        stdout=directory.parent / f"{directory.name}.stdout.log",
        stderr=directory.parent / f"{directory.name}.stderr.log",
    )
    if result.returncode not in (0, 2):
        raise ValueError(
            "Independent verifier rejected the evidence; inspect verifier stderr"
        )
    return (
        read_json(directory / "scorecard.json"),
        read_json(directory / "defects.json"),
        read_json(directory / "observations.json"),
    )


def ensure_unchanged(references):
    for ref in references:
        if digest(ref["path"]) != ref["sha256"]:
            raise ValueError("Frozen campaign evidence changed")
