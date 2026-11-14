"""Optional, source-declared semantic projection stage; no repeated USD export."""

import argparse
import os
import re
import sys
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference, resolve_ref
from simready.core.validation import number
from simready.execution.processes.local import run_process


def plan_decomposition(config, base, video):
    from simready.workflows.contracts.validation import fields, file_path
    from simready.workflows.stages.physical_estimation import references

    fields(
        config,
        {
            "provider",
            "manifest",
            "calibration",
            "association",
            "min_points",
            "min_score",
            "timeout_s",
        },
        "decomposition",
    )
    if config.get("provider") != "semantic_projection":
        raise ValueError("Unregistered decomposition provider")
    manifest = file_path(config.get("manifest"), base)
    calibration = file_path(config.get("calibration"), base)
    association = fields(
        config.get("association"),
        {"source_video_sha256", "reconstruction_sha256", "reason", "evidence"},
        "decomposition association",
    )
    if association.get("source_video_sha256") != digest(video):
        raise ValueError("Decomposition association belongs to another video")
    if not re.fullmatch(
        r"[a-f0-9]{64}", str(association.get("reconstruction_sha256", ""))
    ):
        raise ValueError("Decomposition requires a reconstruction SHA256")
    if (
        not isinstance(association.get("reason"), str)
        or not association["reason"].strip()
    ):
        raise ValueError("Decomposition requires an explicit association reason")
    evidence = resolve_ref(association.get("evidence"), base)
    frozen = {manifest, calibration, evidence}
    for path in (manifest, calibration):
        frozen.update(
            resolve_ref(ref, path.parent) for ref in references(read_json(path))
        )
    timeout = config.get("timeout_s", 300)
    if number(timeout, "decomposition timeout") <= 0:
        raise ValueError("decomposition timeout must be positive")
    minimum = config.get("min_points", 16)
    score = config.get("min_score", 0.25)
    if (
        type(minimum) is not int
        or minimum < 1
        or not 0 <= number(score, "min_score") <= 1
    ):
        raise ValueError("Invalid semantic projection thresholds")
    return {
        "provider": "semantic_projection",
        "manifest": str(manifest),
        "calibration": str(calibration),
        "association": {**association, "evidence": reference(evidence)},
        "min_points": minimum,
        "min_score": score,
        "timeout_s": timeout,
        "dependencies": [reference(p) for p in sorted(frozen)],
    }


def produce_decomposition(plan, reconstruction, spec, target):
    if plan["association"]["reconstruction_sha256"] != reconstruction["ply_sha256"]:
        raise ValueError(
            "Semantic observation is not associated with this reconstruction"
        )
    for ref in plan["dependencies"]:
        resolve_ref(ref, Path.cwd())
    target.mkdir(parents=True)
    write_json(target / "base-spec.json", spec)
    request = {
        "ply": reconstruction["ply_path"],
        "ply_sha256": reconstruction["ply_sha256"],
        "plan": plan,
        "base_spec": reference(target / "base-spec.json"),
    }
    write_json(target / "request.json", request)
    result = run_process(
        [
            sys.executable,
            "-m",
            "simready.workflows.stages.semantic_stage",
            "--request",
            str(target / "request.json"),
            "--out",
            str(target / "projection"),
        ],
        cwd=None,
        timeout=plan["timeout_s"],
        env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
        stdout=target / "stdout.log",
        stderr=target / "stderr.log",
    )
    if result.returncode:
        raise RuntimeError("Semantic projection failed; inspect stage stderr.log")
    for ref in plan["dependencies"]:
        resolve_ref(ref, Path.cwd())
    report = read_json(target / "projection/projection.json")
    write_json(
        target / "binding.json",
        {
            "source_video_sha256": plan["association"]["source_video_sha256"],
            "reconstruction_sha256": reconstruction["ply_sha256"],
            "base_spec_sha256": digest(target / "base-spec.json"),
            "scene_spec_sha256": digest(target / "projection/scene_spec.json"),
            "projection_sha256": digest(target / "projection/projection.json"),
            "association_kind": "operator_declared",
            "semantic_accuracy": "NOT_EVALUATED",
            "status": report["status"],
        },
    )
    return {
        "directory": str(target),
        "scene_spec": str(target / "projection/scene_spec.json"),
    }, [p for p in target.rglob("*") if p.is_file() and p.stat().st_size]


def verify_decomposition(result, artifacts, *, root, plan, reconstruction, spec):
    from simready.workflows.contracts.stage_ledger import checked_artifact

    directory = Path(result["directory"]).resolve()
    recorded = {checked_artifact(root, r) for r in artifacts}
    scene = directory / "projection/scene_spec.json"
    required = {
        scene,
        directory / "base-spec.json",
        directory / "binding.json",
        directory / "request.json",
        directory / "projection/projection.json",
        directory / "projection/point_assignments.npz",
    }
    if (
        not directory.is_relative_to(root)
        or not required <= recorded
        or result["scene_spec"] != str(scene)
    ):
        raise ValueError("Incomplete decomposition artifacts")
    if read_json(directory / "base-spec.json") != spec:
        raise ValueError("Decomposition used another scene specification")
    request = read_json(directory / "request.json")
    if request["plan"] != plan or request["ply_sha256"] != reconstruction["ply_sha256"]:
        raise ValueError("Decomposition request changed")
    for ref in plan["dependencies"]:
        resolve_ref(ref, Path.cwd())
    binding = read_json(directory / "binding.json")
    report = read_json(directory / "projection/projection.json")
    if report.get("files") != {
        "scene_spec": "scene_spec.json",
        "point_assignments": "point_assignments.npz",
    }:
        raise ValueError("Decomposition point assignment evidence is incomplete")
    expected = {
        "source_video_sha256": plan["association"]["source_video_sha256"],
        "reconstruction_sha256": reconstruction["ply_sha256"],
        "base_spec_sha256": digest(directory / "base-spec.json"),
        "scene_spec_sha256": digest(scene),
        "projection_sha256": digest(directory / "projection/projection.json"),
        "association_kind": "operator_declared",
        "semantic_accuracy": "NOT_EVALUATED",
        "status": read_json(directory / "projection/projection.json")["status"],
    }
    if binding != expected:
        raise ValueError("Decomposition evidence changed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    request = read_json(args.request)
    plan = request["plan"]
    if digest(request["ply"]) != request["ply_sha256"]:
        raise ValueError("Reconstruction changed")
    spec = resolve_ref(request["base_spec"], args.request.parent)
    from simready.assets.association.semantic_projection import build_semantic_spec

    build_semantic_spec(
        request["ply"],
        plan["manifest"],
        plan["calibration"],
        args.out,
        scene_spec=spec,
        min_points=plan["min_points"],
        min_score=plan["min_score"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
