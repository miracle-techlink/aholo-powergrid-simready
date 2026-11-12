"""Evidence-bound runtime recipes; failures remain reusable negative memory."""

import json
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json
from simready.core.evidence import ensure_unchanged, reference
from simready.evaluation.performance_gate import check_quality, compare_runtime
from simready.experience.database import ExperienceDatabase


class PerformanceMemory:
    """Keep runtime optimization evidence separate from scene-repair skills."""

    def __init__(self, path: Path) -> None:
        self.database = ExperienceDatabase(path)

    def append(self, record: dict) -> dict:
        value = dict(record)
        ensure_unchanged(value["evidence"])
        bound_paths = {str(Path(item["path"]).resolve()) for item in value["evidence"]}
        required = [
            value["protocol"],
            value["calibration"],
            *value["quality_reports"],
            *value["baseline_profiles"],
            *value["candidate_profiles"],
        ]
        if any(str(Path(path).resolve()) not in bound_paths for path in required):
            raise ValueError("Unbound performance evidence")
        protocol = read_json(value["protocol"])
        if object_digest(protocol) != value["benchmark_key"]:
            raise ValueError("Performance memory protocol identity mismatch")
        if value["candidate_id"] not in {x["id"] for x in protocol["candidates"]}:
            raise ValueError("Unregistered runtime candidate")
        registered = next(
            item
            for item in protocol["candidates"]
            if item["id"] == value["candidate_id"]
        )
        if value["recipe"] != registered:
            raise ValueError("Runtime recipe differs from its registered candidate")
        calibration = read_json(value["calibration"])
        for field in ("baseline_profiles", "candidate_profiles", "quality_reports"):
            if len({Path(path).resolve() for path in value[field]}) != len(
                value[field]
            ):
                raise ValueError("Repeated evidence is not an independent trial")
        for field in ("baseline_videos", "candidate_videos"):
            ensure_unchanged(value[field])
            if any(
                str(Path(ref["path"]).resolve()) not in bound_paths
                for ref in value[field]
            ):
                raise ValueError("Unbound runtime video evidence")
        if len(value["quality_reports"]) > len(value["candidate_videos"]):
            raise ValueError("More quality reports than candidate trials")
        for index, path in enumerate(value["quality_reports"]):
            report = read_json(path)
            if (
                report["protocol_sha256"] != digest(value["protocol"])
                or report["before_sha256"] != value["baseline_videos"][0]["sha256"]
                or report["after_sha256"] != value["candidate_videos"][index]["sha256"]
            ):
                raise ValueError("Independent quality report binding mismatch")
        for field, output in (
            ("baseline_profiles", "baseline_seconds"),
            ("candidate_profiles", "candidate_seconds"),
        ):
            profiles = [read_json(path) for path in value[field]]
            expected_variant = (
                protocol["baseline"]["id"]
                if field == "baseline_profiles"
                else value["candidate_id"]
            )
            if any(
                p["status"] != "SUCCESS"
                or p["metadata"]["benchmark_key"] != value["benchmark_key"]
                or p["metadata"]["variant"] != expected_variant
                for p in profiles
            ):
                raise ValueError("Runtime sample provenance mismatch")
            value[output] = [p["wall_seconds"] for p in profiles]
        quality = [
            check_quality(read_json(path)["metrics"], calibration)
            for path in value["quality_reports"]
        ]
        runtime = compare_runtime(
            value["baseline_seconds"],
            value["candidate_seconds"],
            protocol["speed_policy"],
        )
        accepted = (
            len(quality) >= protocol["speed_policy"]["repetitions"]
            and all(x["passed"] for x in quality)
            and runtime["passed"]
            and not value.get("errors")
        )
        value.update(
            quality=quality,
            runtime=runtime,
            outcome="ACCEPTED" if accepted else "REJECTED",
            reuse_scope="exact_benchmark_only",
            cross_scene_skill_active=False,
        )
        key = object_digest(value)
        with self.database.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO performance_experiments VALUES (?,?,?,?,?)",
                (
                    key,
                    value["benchmark_key"],
                    value["candidate_id"],
                    value["outcome"],
                    json.dumps(value, sort_keys=True, allow_nan=False),
                ),
            )
        return dict(value, experiment_id=key)

    def retrieve(self, benchmark_key: str) -> list[dict]:
        """Return only unchanged evidence; no cross-scene performance claims."""
        with self.database.transaction() as db:
            rows = db.execute(
                "SELECT * FROM performance_experiments WHERE benchmark_key=? ORDER BY rowid DESC",
                (benchmark_key,),
            ).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["payload"])
            try:
                if object_digest(value) != row["experiment_id"]:
                    continue
                ensure_unchanged(value["evidence"])
                result.append(dict(value, experiment_id=row["experiment_id"]))
            except (OSError, ValueError):
                continue
        return result

    def bind_evidence(self, paths: list[Path]) -> list[dict]:
        """Fingerprint the complete experiment record before committing memory."""
        return [reference(path) for path in sorted(set(paths))]
