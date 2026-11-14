"""Freeze, package and compile an optional NeRF2Physics stage for video runs."""

import copy
import os
import shutil
import sys
from pathlib import Path

from simready.assets.physics.physical_candidate import compile_candidate
from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import resolve_ref
from simready.core.validation import number
from simready.execution.processes.local import run_process


def references(value):
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield value
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def estimation_plan(candidate, request, output, timeout_s=300):
    if candidate and request:
        raise ValueError("Choose a candidate import or an estimation request")
    source = candidate or request
    if not source:
        return None
    if number(timeout_s, "physical_timeout") <= 0:
        raise ValueError("physical_timeout must be positive")
    source = Path(source).expanduser().resolve()
    dependencies, pending = set(), [source]
    while pending:
        path = pending.pop()
        if path in dependencies:
            continue
        dependencies.add(path)
        if path == source or (request and path.suffix.lower() == ".json"):
            pending.extend(
                resolve_ref(ref, path.parent) for ref in references(read_json(path))
            )
    dependencies = sorted(dependencies)
    if any(path.is_relative_to(output) for path in dependencies):
        raise ValueError("Physical estimation inputs must be outside the run")
    return {
        "mode": "import" if candidate else "estimate",
        "path": str(source),
        "timeout_s": timeout_s,
        "dependencies": [
            {"path": str(path), "sha256": digest(path)} for path in dependencies
        ],
    }


def _unchanged(plan):
    for ref in plan["dependencies"]:
        resolve_ref(ref, Path.cwd())


def _validate_evidence(value, root):
    if not isinstance(value, dict):
        raise ValueError("Physical candidate must be an object")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(
            "Physical candidate requires a nonempty evidence reference list"
        )
    association = value.get("association")
    method = value.get("method")
    if not isinstance(association, dict) or not isinstance(method, dict):
        raise ValueError("Physical candidate requires association and method objects")
    parameters = method.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("Physical candidate requires method parameters")
    for ref in [
        *evidence,
        association.get("evidence"),
        parameters.get("scale_evidence"),
    ]:
        resolve_ref(ref, root)


def _package(candidate_path, target, operation):
    value = read_json(candidate_path)
    _validate_evidence(value, candidate_path.parent)
    target.mkdir(parents=True)
    shutil.copyfile(candidate_path, target / "source-candidate.json")
    packaged = copy.deepcopy(value)
    for ref in references(packaged):
        source = resolve_ref(ref, candidate_path.parent)
        suffix = (
            source.suffix
            if source.suffix.isascii() and len(source.suffix) < 16
            else ".bin"
        )
        destination = target / "evidence" / (ref["sha256"] + suffix)
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        if digest(destination) != ref["sha256"]:
            raise ValueError("Physical evidence changed during packaging")
        ref["path"] = str(destination.relative_to(target))
    packaged["packaging"] = {
        "operation": operation,
        "source_candidate": {
            "path": "source-candidate.json",
            "sha256": digest(candidate_path),
        },
    }
    write_json(target / "physical-candidate.json", packaged)
    return target / "physical-candidate.json"


def produce_estimation(plan, spec, video_sha256, ply_sha256, target):
    _unchanged(plan)
    target.mkdir(parents=True)
    candidate = Path(plan["path"])
    if plan["mode"] == "estimate":
        process = run_process(
            [
                sys.executable,
                "-m",
                "simready.ingestion.nerf2physics",
                "--request",
                str(candidate),
                "--out",
                str(target / "model"),
            ],
            cwd=None,
            timeout=plan["timeout_s"],
            stdout=target / "estimation.stdout.log",
            stderr=target / "estimation.stderr.log",
            env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
        )
        if process.returncode:
            raise RuntimeError(
                "Physical estimator failed; inspect estimation.stderr.log"
            )
        candidate = target / "model/physical-candidate.json"
    candidate = _package(candidate, target / "physical", plan["mode"])
    compiled, report = compile_candidate(
        spec,
        read_json(candidate),
        candidate_sha256=digest(candidate),
        source_video_sha256=video_sha256,
        reconstruction_sha256=ply_sha256,
    )
    write_json(target / "physical/base-spec.json", spec)
    write_json(target / "physical/compiled-spec.json", compiled)
    write_json(target / "physical/compilation.json", report)
    _unchanged(plan)
    # Empty stderr/stdout are legitimate diagnostics, not required stage artifacts.
    return {"directory": str(target / "physical")}, [
        p for p in sorted(target.rglob("*")) if p.is_file() and p.stat().st_size
    ]


def verify_compilation(directory, compiled_spec, video_sha256, ply_sha256):
    directory = Path(directory)
    candidate_path = directory / "physical-candidate.json"
    candidate = read_json(candidate_path)
    _validate_evidence(candidate, directory)
    for ref in references(candidate):
        resolve_ref(ref, directory)
    expected, report = compile_candidate(
        read_json(directory / "base-spec.json"),
        candidate,
        candidate_sha256=digest(candidate_path),
        source_video_sha256=video_sha256,
        reconstruction_sha256=ply_sha256,
    )
    if (
        compiled_spec != expected
        or read_json(directory / "compiled-spec.json") != expected
        or read_json(directory / "compilation.json") != report
    ):
        raise ValueError(
            "Physical compilation is not bound to its source candidate and scene"
        )
    return {
        "candidate_sha256": digest(candidate_path),
        "report_sha256": digest(directory / "compilation.json"),
    }


def verify_estimation(result, artifacts, *, root, spec, video_sha256, ply_sha256):
    directory = Path(result["directory"]).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Physical compilation escaped its run")
    recorded = {(root / item["path"]).resolve() for item in artifacts}
    required = {p for p in directory.rglob("*") if p.is_file()}
    if (
        not required
        or not required <= recorded
        or read_json(directory / "base-spec.json") != spec
    ):
        raise ValueError("Physical stage has incomplete evidence or another scene")
    verify_compilation(
        directory, read_json(directory / "compiled-spec.json"), video_sha256, ply_sha256
    )
