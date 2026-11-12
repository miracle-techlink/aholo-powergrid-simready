"""Verifier entry: recalculate measurements, apply pinned checks, emit evidence."""

from __future__ import annotations

import platform
import time
from pathlib import Path

from ..artifacts import digest, write_json
from . import VERSION
from .contracts import (
    fingerprint,
    finite,
    read_json,
    require,
    validate_candidate,
    validate_protocol,
)
from .observer import observe


def score(protocol, observations):
    checks = []
    for spec in protocol["checks"]:
        value = observations["metrics"].get(spec["metric"])
        if not finite(value):
            state, reason = "INCOMPLETE", "MISSING_MEASUREMENT"
        elif spec["limit"] is None or spec["calibration"] == "pending":
            state, reason = "INCOMPLETE", "THRESHOLD_NOT_CALIBRATED"
        else:
            passed = {
                ">=": value >= spec["limit"],
                "<=": value <= spec["limit"],
                "==": value == spec["limit"],
            }[spec["op"]]
            state, reason = (
                ("PASS", "WITHIN_LIMIT") if passed else ("FAIL", "OUTSIDE_LIMIT")
            )
        checks.append({**spec, "value": value, "status": state, "reason": reason})
    required = [c for c in checks if c["required"]]
    status = (
        "FAIL"
        if any(c["status"] == "FAIL" for c in required)
        else "INCOMPLETE"
        if any(c["status"] != "PASS" for c in required)
        else "PASS"
    )
    tracks = {}
    for name in sorted({c["track"] for c in checks}):
        rows = [c for c in required if c["track"] == name]
        tracks[name] = (
            "NOT_REQUIRED"
            if not rows
            else "FAIL"
            if any(c["status"] == "FAIL" for c in rows)
            else "INCOMPLETE"
            if any(c["status"] != "PASS" for c in rows)
            else "PASS"
        )
    return {
        "status": status,
        "checks": checks,
        "tracks": tracks,
        "blocking_checks": [c["check_id"] for c in required if c["status"] != "PASS"],
        "missing_checks": [
            c["check_id"] for c in required if c["status"] == "INCOMPLETE"
        ],
    }


def defects_from_checks(checks, observations):
    defects = []
    for check in checks:
        if not check["required"] or check["status"] == "PASS":
            continue
        evidence = []
        # Localize a visual mismatch without asserting that blur is its root cause.
        if check["status"] == "FAIL" and check["metric"].startswith("appearance."):
            metric = check["metric"].split(".")[1]
            for region in observations["regions"]:
                value = region["metrics"].get(metric)
                if value is not None and value < check["limit"]:
                    evidence.append(
                        {k: region[k] for k in ("view_id", "asset_id", "bbox_xyxy")}
                    )
        if check["status"] == "FAIL" and check["metric"].startswith("assets."):
            kinds = {
                "missing_count": {"missing_asset"},
                "missing_critical_count": {"missing_asset"},
                "extra_count": {"extra_asset"},
                "category_error_count": {"wrong_asset_category"},
                "level_error_count": {"wrong_asset_level"},
                "parent_error_count": {"wrong_asset_parent"},
                "material_error_count": {"wrong_asset_material"},
                "state_error_count": {"wrong_asset_state"},
                "affordance_error_count": {"wrong_asset_affordances"},
                "position_error_max_m": {"asset_position_error"},
                "size_relative_error_max": {"asset_size_error"},
                "orientation_error_max_deg": {"asset_orientation_error"},
                "relation_error_count": {
                    "missing_asset_relation",
                    "extra_asset_relation",
                },
                "unknown_source_property_count": {"unknown_source_property"},
                "missing_property_count": {"missing_asset_property"},
            }.get(check["metric"].split(".")[1], set())
            for item in (observations.get("asset_graph") or {}).get("details", []):
                if item["kind"] in kinds and (
                    "error" not in item or item["error"] > check["limit"]
                ):
                    evidence.append(item)
        record = {
            "check_id": check["check_id"],
            "kind": check.get("defect_kind", "unclassified"),
            "status": "OPEN",
            "severity": check["severity"],
            "track": check["track"],
            "reason": check["reason"],
            "metric": check["metric"],
            "measured": check["value"],
            "criterion": {"op": check["op"], "limit": check["limit"]},
            "asset_ids": sorted({e["asset_id"] for e in evidence}) or ["scene"],
            "evidence": evidence,
            "root_cause": "UNCONFIRMED",
            "repairable_now": check["status"] == "FAIL"
            and check.get("defect_kind")
            not in ("missing_observation", "source_evidence"),
        }
        record["defect_id"] = fingerprint(
            {"check": check["check_id"], "assets": record["asset_ids"]}
        )[:20]
        defects.append(record)
    return defects


def evaluate(
    protocol_path,
    candidate_path,
    output,
    expected_protocol=None,
    expected_candidate=None,
):
    start = time.perf_counter()
    protocol_path, candidate_path = (
        Path(protocol_path).resolve(),
        Path(candidate_path).resolve(),
    )
    output = Path(output).resolve()
    require(not output.exists(), f"Output must be a new directory: {output}")
    files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path(__file__).parents[1] / "artifacts.py"
    ]
    code = {f.name: (f, digest(f)) for f in files}
    protocol_hash, candidate_hash = digest(protocol_path), digest(candidate_path)
    require(
        expected_protocol in (None, protocol_hash),
        "Protocol changed before verification",
    )
    require(
        expected_candidate in (None, candidate_hash),
        "Candidate changed before verification",
    )
    p = validate_protocol(read_json(protocol_path))
    c = validate_candidate(read_json(candidate_path), protocol_hash, p)
    observations = observe(p, c, protocol_path.parent, candidate_path.parent)
    # Refuse to publish a mixed snapshot if anything changed during measurement.
    require(
        digest(protocol_path) == protocol_hash
        and digest(candidate_path) == candidate_hash,
        "Contract changed during verification",
    )
    for ref in observations["input_files"]:
        require(
            digest(ref["path"]) == ref["sha256"], "Input changed during verification"
        )
    for path, sha in code.values():
        require(digest(path) == sha, "Verifier code changed during verification")
    result = score(p, observations)
    binding = {
        "benchmark_id": p["benchmark_id"],
        "profile": p["profile"],
        "phase": p["phase"],
        "protocol_sha256": protocol_hash,
        "candidate_sha256": candidate_hash,
        "candidate_id": c["candidate_id"],
    }
    observation_bundle = {"schema_version": 1, **binding, **observations}
    defects = defects_from_checks(result["checks"], observations)
    output.mkdir(parents=True)
    (output / "verifier_code").mkdir()
    for name, (path, sha) in code.items():
        snapshot = output / "verifier_code" / name
        snapshot.write_bytes(path.read_bytes())
        require(digest(snapshot) == sha, "Verifier snapshot changed during copy")
    write_json(output / "observations.json", observation_bundle)
    # Sealed validation never produces detailed repair feedback for the agent.
    # Files still require OS-level protection; this conditional is not a sandbox.
    if p["phase"] == "development":
        write_json(
            output / "defects.json",
            {"schema_version": 1, **binding, "defects": defects},
        )
    result.update(
        schema_version=1,
        **binding,
        evaluator=VERSION,
        evaluator_files={name: sha for name, (_, sha) in code.items()},
        observations_sha256=digest(output / "observations.json"),
        candidate_evidence_status=c.get("evidence_status", "unverified"),
        metric_scope="Raw input recomputation; candidate/render provenance not independently authenticated",
        release_authorized=False,
        release_blockers=[
            "Isolated trusted renderer/data/publisher service is not deployed",
            "Development or legacy results cannot certify sealed release",
        ],
        wall_seconds=time.perf_counter() - start,
        python_version=platform.python_version(),
        input_tokens=None,
        output_tokens=None,
        note="No LLM calls made by this verifier; historical pipeline token usage is unknown.",
    )
    write_json(output / "scorecard.json", result)
    return result
