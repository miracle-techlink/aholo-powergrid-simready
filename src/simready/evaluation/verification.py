"""Verifier process boundary and immutable evidence references."""

import sys
from pathlib import Path

from simready.core.artifacts import digest, read_json
from simready.core.types import JsonObject, PathLike
from simready.execution.processes import run_process


def verify_candidate(
    protocol: PathLike,
    candidate: PathLike,
    directory: PathLike,
    *,
    protocol_hash: str,
    timeout: float,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    """Run a fresh verifier process; return scorecard, defects and observations."""
    protocol, candidate = Path(protocol).resolve(), Path(candidate).resolve()
    directory = Path(directory).resolve()
    command = [
        sys.executable,
        "-m",
        "simready.evaluation",
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
        cwd=directory.parent,
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
