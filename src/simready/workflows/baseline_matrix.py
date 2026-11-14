"""Sequential, frozen pipeline comparisons; no scheduler or automatic ranking.

Input declarations and raw-file checks establish identity, not independent proof
of training isolation or render provenance. Synthetic fixtures test interfaces.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

from simready.core.artifacts import object_digest, read_json, write_json
from simready.core.evidence import ensure_unchanged, reference, resolve_ref
from simready.core.reconstruction_contract import verify_bundle
from simready.core.validation import number
from simready.evaluation.protocol import validate_candidate, validate_protocol
from simready.evaluation.verification import verify_candidate
from simready.execution.processes import ProcessCancelledError
from simready.workflows.pipeline_spec import fields, load_pipeline
from simready.workflows.stage_ledger import failure_status
from simready.workflows.video_pipeline import execute_video, make_plan


def _references(value, base):
    """Freeze nested file references using the containing document's directory."""
    found = []
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            found.append(reference(resolve_ref(value, base)))
        else:
            for item in value.values():
                found.extend(_references(item, base))
    elif isinstance(value, list):
        for item in value:
            found.extend(_references(item, base))
    return found


def _content(value):
    """Path relocation does not change a referenced file's content identity."""
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            return {"sha256": value["sha256"]}
        return {key: _content(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_content(item) for item in value]
    return value


def comparison_status(members, mode, camera_evidence):
    reasons, signatures = [], []
    for member in members:
        plan = member["plan"]
        config = plan["pipeline"]["reconstruction"]["config"]
        frames, coordinates = config["frames"], config["coordinates"]
        algorithm = config["algorithm"]
        if not algorithm.get("version") or not algorithm.get("implementation_sha256"):
            reasons.append(f"{member['id']}: algorithm identity incomplete")
        if frames["status"] != "declared" or not all(
            frames[key] for key in ("training", "heldout")
        ):
            reasons.append(f"{member['id']}: training/heldout declaration incomplete")
        if mode == "fixed_observations" and (
            coordinates["scale_status"] != "verified"
            or coordinates["frame"] == "unknown"
            or coordinates["unit"] not in {"m", "cm", "mm"}
        ):
            reasons.append(f"{member['id']}: camera/metric scale evidence incomplete")
        stages = {
            name: {key: value for key, value in recipe.items() if key != "dependencies"}
            for name, recipe in plan["stage_contracts"]["recipes"].items()
            if name != "reconstruction"
        }
        signatures.append(
            object_digest(
                _content(
                    {"stages": stages, "frames": frames, "coordinates": coordinates}
                )
            )
        )
    if mode == "fixed_observations":
        if camera_evidence is None:
            reasons.append("Shared camera evidence missing")
        if len(set(signatures)) != 1:
            reasons.append(
                "Non-reconstruction configuration or frame/scale declarations differ"
            )
    return {
        "status": "COMPARABILITY_UNCONFIRMED" if reasons else "DECLARED_INPUTS_MATCH",
        "mode": mode,
        "reasons": reasons,
        "independent_training_isolation_verified": False,
        "render_provenance_independently_verified": False,
        "scope": "Content identities and caller declarations only; backends may access the complete video",
    }


def plan_matrix(
    config,
    output,
    *,
    until="reconstruction",
    accept_eula=False,
    submit_reconstruction=False,
):
    config, output = Path(config).resolve(), Path(output).absolute()
    if output.is_symlink():
        raise ValueError("Matrix output must not be a symlink")
    output = output.resolve()
    raw = read_json(config)
    fields(
        raw,
        {"schema_version", "mode", "purpose", "video", "members", "evaluation"},
        "matrix",
    )
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("Expected matrix schema_version=1")
    mode = raw.get("mode")
    if mode not in {"fixed_observations", "end_to_end"}:
        raise ValueError("Matrix mode must be fixed_observations or end_to_end")
    purpose = raw.get("purpose", "interface_validation")
    if purpose not in {"interface_validation", "quality_comparison"}:
        raise ValueError("Unknown matrix purpose")
    if until not in {None, "reconstruction"}:
        raise ValueError("Matrix supports until=reconstruction or an explicit full run")
    video = reference(resolve_ref(raw.get("video"), config.parent))
    evaluation = fields(
        raw.get("evaluation", {}),
        {"protocol", "camera_evidence", "timeout_s"},
        "evaluation",
    )
    timeout = evaluation.get("timeout_s", 300)
    if number(timeout, "evaluation timeout") <= 0:
        raise ValueError("Evaluation timeout must be positive")
    frozen = [reference(config), video]
    protocol_ref = camera_ref = protocol = None
    if evaluation.get("protocol") is not None:
        protocol_path = resolve_ref(evaluation["protocol"], config.parent)
        protocol_ref = reference(protocol_path)
        protocol = validate_protocol(read_json(protocol_path))
        frozen += [protocol_ref, *_references(protocol, protocol_path.parent)]
    if evaluation.get("camera_evidence") is not None:
        camera_ref = reference(
            resolve_ref(evaluation["camera_evidence"], config.parent)
        )
        frozen.append(camera_ref)
    rows = raw.get("members")
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("Matrix requires at least two members")
    members, names = [], set()
    for row in rows:
        fields(row, {"id", "pipeline", "candidate"}, "member")
        name = row.get("id")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name)
            or name in names
        ):
            raise ValueError("Member IDs must be unique safe directory names")
        names.add(name)
        path = resolve_ref(row.get("pipeline"), config.parent)
        pipeline_ref = reference(path)
        args = load_pipeline(
            path,
            output / "members" / name,
            until=until,
            accept_eula=accept_eula,
            submit_reconstruction=submit_reconstruction,
        )
        plan = make_plan(args)
        if plan["source_video_sha256"] != video["sha256"]:
            raise ValueError("Every matrix member must use the same frozen video")
        candidate_ref = None
        if row.get("candidate") is not None:
            if protocol is None:
                raise ValueError(
                    "Candidate evidence requires a shared evaluation protocol"
                )
            candidate_path = resolve_ref(row["candidate"], config.parent)
            candidate_ref = reference(candidate_path)
            candidate = read_json(candidate_path)
            validate_candidate(candidate, protocol_ref["sha256"], protocol)
            frozen += [candidate_ref, *_references(candidate, candidate_path.parent)]
        frozen += [pipeline_ref, *_references(plan, path.parent)]
        members.append(
            {
                "id": name,
                "pipeline": pipeline_ref,
                "candidate": candidate_ref,
                "plan": plan,
            }
        )
    frozen = sorted(
        {(ref["path"], ref["sha256"]): ref for ref in frozen}.values(),
        key=lambda ref: ref["path"],
    )
    if any(Path(ref["path"]).is_relative_to(output) for ref in frozen):
        raise ValueError("Matrix output must not contain frozen inputs")
    manifest = {
        "schema_version": 1,
        "mode": mode,
        "purpose": purpose,
        "video": video,
        "out": str(output),
        "until": until,
        "execution_authorization": {
            "accept_eula": bool(accept_eula),
            "submit_reconstruction": bool(submit_reconstruction),
        },
        "members": members,
        "frozen_references": frozen,
        "evaluation": {
            "protocol": protocol_ref,
            "camera_evidence": camera_ref,
            "timeout_s": timeout,
            "protocol_is_caller_supplied": True,
        },
        "comparison": comparison_status(members, mode, camera_ref),
        "ranking": None,
        "default_provider_promotion": None,
    }
    manifest["identity"] = object_digest(manifest)
    return manifest


def _quality(member, manifest, directory, reconstruction):
    evaluation = manifest["evaluation"]
    unknown = {
        "status": "NOT_EVALUATED",
        "reason": "Real heldout render/reference evidence not supplied",
    }
    if (
        manifest["purpose"] != "quality_comparison"
        or not member["candidate"]
        or not evaluation["protocol"]
    ):
        return unknown
    protocol_path = Path(evaluation["protocol"]["path"])
    protocol = read_json(protocol_path)
    if any(view["reference_kind"] != "real_capture" for view in protocol["views"]):
        return {
            **unknown,
            "reason": "Synthetic or estimated references do not establish model quality",
        }
    bundle = verify_bundle(
        reconstruction["bundle_path"], source_video_sha256=manifest["video"]["sha256"]
    )
    if "synthetic" in bundle["algorithm"]["name"].lower():
        return {
            **unknown,
            "reason": "Synthetic interface fixture is not a model quality baseline",
        }
    frames = bundle["frames"]
    if (
        frames["status"] != "declared"
        or not frames["training"]
        or not frames["heldout"]
    ):
        return {
            **unknown,
            "reason": "Training and heldout frame declarations are required",
        }
    candidate_path = Path(member["candidate"]["path"])
    candidate = read_json(candidate_path)
    heldout = {(item["id"], item["sha256"]) for item in frames["heldout"]}
    if (
        not {(view["frame_id"], view["rgb"]["sha256"]) for view in protocol["views"]}
        <= heldout
    ):
        raise ValueError(
            "Evaluation views are not declared heldout reconstruction frames"
        )
    for document in (protocol, candidate):
        if set(document.get("fit_frame_ids", [])) != {
            item["id"] for item in frames["training"]
        } or set(document.get("fit_image_sha256", [])) != {
            item["sha256"] for item in frames["training"]
        }:
            raise ValueError(
                "Evaluation fit declarations differ from reconstruction training frames"
            )
    if reconstruction["ply_sha256"] not in {
        item["sha256"] for item in candidate["artifacts"]
    }:
        raise ValueError(
            "Candidate evidence is not bound to this reconstruction artifact"
        )
    camera = evaluation["camera_evidence"]
    binding = candidate.get("comparison_evidence", {})
    if not camera or binding != {
        "source_video_sha256": manifest["video"]["sha256"],
        "reconstruction_sha256": reconstruction["ply_sha256"],
        "camera_sha256": camera["sha256"],
    }:
        raise ValueError(
            "Candidate needs explicit source/reconstruction/camera comparison_evidence"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    scorecard, feedback, _ = verify_candidate(
        protocol_path,
        candidate_path,
        directory,
        protocol_hash=evaluation["protocol"]["sha256"],
        timeout=evaluation["timeout_s"],
    )
    return {
        "status": "MEASURED",
        "scorecard": reference(directory / "scorecard.json"),
        "verifier_status": scorecard["status"],
        "feedback_status": feedback.get("status", "DEVELOPMENT"),
        "independently_checked": "Raw evidence hashes and metrics recomputed by existing verifier",
        "caller_declared": "Training access, camera correspondence and render provenance",
        "release_authorized": False,
    }


def execute_matrix(manifest):
    """Run frozen members in order, preserving failed and interrupted rows."""
    root = Path(manifest["out"])
    if root.exists() or root.is_symlink():
        raise FileExistsError("Choose a fresh matrix output directory")
    expected = dict(manifest)
    identity = expected.pop("identity")
    if object_digest(expected) != identity:
        raise ValueError("Matrix manifest identity mismatch")
    ensure_unchanged(manifest["frozen_references"])
    root.mkdir(parents=True)
    write_json(root / "matrix-plan.json", manifest)
    report = {
        "schema_version": 1,
        "matrix_identity": identity,
        "status": "RUNNING",
        "mode": manifest["mode"],
        "purpose": manifest["purpose"],
        "until": manifest["until"],
        "comparison": copy.deepcopy(manifest["comparison"]),
        "ranking": None,
        "default_provider_promotion": None,
        "members": [
            {
                "id": member["id"],
                "status": "NOT_RUN",
                "quality": {"status": "NOT_EVALUATED"},
            }
            for member in manifest["members"]
        ],
    }
    write_json(root / "matrix-result.json", report)
    try:
        for member, row in zip(manifest["members"], report["members"], strict=True):
            try:
                ensure_unchanged(manifest["frozen_references"])
                args = load_pipeline(
                    Path(member["pipeline"]["path"]),
                    Path(member["plan"]["out"]),
                    until=manifest["until"],
                    **manifest["execution_authorization"],
                )
                if make_plan(args) != member["plan"]:
                    raise ValueError(
                        "Frozen pipeline implementation or configuration changed"
                    )
                row["status"] = "RUNNING"
                write_json(root / "matrix-result.json", report)
                state, code = execute_video(args, member["plan"])
                row.update(
                    status=state["status"],
                    exit_code=code,
                    completed_pipeline=code == 0 and state["status"] == "COMPLETE",
                )
                if state["status"] == "CANCELLED":
                    raise ProcessCancelledError(0)
                if state["status"] == "INTERRUPTED":
                    raise KeyboardInterrupt()
                if code:
                    row["error"] = {
                        "type": "PipelineFailure",
                        "message": state.get("error", state["status"]),
                    }
                ensure_unchanged(manifest["frozen_references"])
                if make_plan(args) != member["plan"]:
                    raise ValueError(
                        "Pipeline inputs or implementation changed during execution"
                    )
                if code == 0:
                    receipt = read_json(
                        Path(member["plan"]["out"]) / "stages/reconstruction.json"
                    )
                    row["reconstruction"] = receipt["result"]
                    row["quality"] = _quality(
                        member,
                        manifest,
                        root / "evaluation" / member["id"],
                        receipt["result"],
                    )
                    ensure_unchanged(manifest["frozen_references"])
            except BaseException as error:
                row.update(
                    status=failure_status(error),
                    error={"type": type(error).__name__, "message": str(error)},
                )
                row["quality"] = {
                    "status": "NOT_EVALUATED",
                    "reason": "Member execution or evidence validation failed",
                }
                if not isinstance(error, Exception) or row["status"] in {
                    "CANCELLED",
                    "INTERRUPTED",
                }:
                    raise
            finally:
                write_json(root / "matrix-result.json", report)
        report["status"] = (
            "COMPLETE"
            if all(
                row.get("exit_code") == 0 and "error" not in row
                for row in report["members"]
            )
            else "COMPLETED_WITH_FAILURES"
        )
    except BaseException as error:
        report["status"] = failure_status(error)
        raise
    finally:
        report["counts"] = {
            "planned": len(report["members"]),
            "not_run": sum(row["status"] == "NOT_RUN" for row in report["members"]),
            "quality_measured": sum(
                row["quality"]["status"] == "MEASURED" for row in report["members"]
            ),
        }
        write_json(root / "matrix-result.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--until", choices=["reconstruction"], default="reconstruction")
    scope.add_argument(
        "--full",
        action="store_true",
        help="Explicitly run the complete configured pipelines",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument("--submit-reconstruction", action="store_true")
    args = parser.parse_args(argv)
    plan = plan_matrix(
        args.matrix,
        args.out,
        until=None if args.full else args.until,
        accept_eula=args.accept_eula,
        submit_reconstruction=args.submit_reconstruction,
    )
    result = plan if args.dry_run else execute_matrix(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.dry_run or result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
