"""Append-only, evidence-bound episode memory. Safe for multiple local writers."""

import json
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json
from simready.core.evidence import resolve_ref
from simready.core.types import JsonObject, PathLike
from simready.evaluation.regression import compare_scorecards
from simready.experience.database import ExperienceDatabase


def verify_reference(reference):
    path = Path(reference["path"])
    if not path.is_absolute() or digest(path) != reference["sha256"]:
        raise ValueError("Memory evidence missing or changed")
    return path


def source_images(evidence, before):
    """Bind promotion evidence to reference content, independently of scene labels."""
    if "protocol" not in evidence:
        return None  # Legacy episodes remain readable but cannot prove scene diversity.
    path = verify_reference(evidence["protocol"])
    if digest(path) != before["protocol_sha256"]:
        raise ValueError("Memory protocol differs from its scorecard")
    protocol = read_json(path)
    if protocol.get("phase") != "development" or not protocol.get("views"):
        raise ValueError("Memory requires development reference views")
    hashes = set()
    for view in protocol["views"]:
        reference = view["rgb"]
        resolve_ref(reference, path.parent)
        hashes.add(reference["sha256"])
    return sorted(hashes)


class EpisodeStore:
    """Store verified repair episodes and retrieve successes or failures."""

    def __init__(self, path: PathLike) -> None:
        self.database = ExperienceDatabase(path)

    def append(self, episode: JsonObject) -> JsonObject:
        value = dict(episode)
        for name in (
            "scene_id",
            "family",
            "policy_key",
            "candidate_sha256",
            "action_key",
            "plugin_id",
            "plugin_version",
        ):
            if not isinstance(value.get(name), str) or not value[name]:
                raise ValueError(f"Episode requires {name}")
        if value.get("role") not in ("development", "validation"):
            raise ValueError(
                "Memory accepts development/validation roles, never sealed evidence"
            )
        if (
            not isinstance(value.get("defect_kinds"), list)
            or not value["defect_kinds"]
            or any(not isinstance(x, str) for x in value["defect_kinds"])
        ):
            raise ValueError("Episode requires defect kinds")
        if not isinstance(value.get("parameters"), dict):
            raise ValueError("Episode requires parameters")
        evidence = value["evidence"]
        for ref in evidence.values():
            verify_reference(ref)
        before = read_json(evidence["before"]["path"])
        if (
            before.get("phase") != "development"
            or before["candidate_sha256"] != value["candidate_sha256"]
        ):
            raise ValueError("Episode candidate or phase binding mismatch")
        sources = source_images(evidence, before)
        if sources is not None:
            value["source_images_sha256"] = sources
        else:
            value.pop("source_images_sha256", None)
        if "after" in evidence:
            after = read_json(evidence["after"]["path"])
            if after.get("phase") != "development":
                raise ValueError("Sealed evidence cannot enter memory")
            value["comparison"] = compare_scorecards(before, after)
            value["outcome"] = (
                "ACCEPTED" if value["comparison"]["accepted"] else "REJECTED"
            )
        else:
            value["comparison"] = None
            value["outcome"] = "ERROR"
        key = object_digest(value)
        encoded = json.dumps(value, sort_keys=True, allow_nan=False)
        with self.database.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO episodes VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    value["family"],
                    value["policy_key"],
                    value["scene_id"],
                    value["candidate_sha256"],
                    value["action_key"],
                    value["outcome"],
                    encoded,
                ),
            )
        return dict(value, episode_id=key)

    @staticmethod
    def decode(row):
        value = json.loads(row["payload"])
        if object_digest(value) != row["episode_id"]:
            raise ValueError("Memory payload identity changed")
        for ref in value["evidence"].values():
            verify_reference(ref)
        # Historical callers could supply a protocol before source hashes were
        # derived. Keep those records readable without upgrading their evidence.
        if "source_images_sha256" in value:
            sources = source_images(
                value["evidence"], read_json(value["evidence"]["before"]["path"])
            )
            if value["source_images_sha256"] != sources:
                raise ValueError("Memory source evidence changed")
        return dict(value, episode_id=row["episode_id"])

    def get(self, episode_id: str) -> JsonObject:
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown episode")
        return self.decode(row)

    def retrieve(
        self, family: str, policy_key: str, defect_kinds: list[str], limit: int = 8
    ) -> list[JsonObject]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Retrieval limit must be between 1 and 100")
        with self.database.transaction() as db:
            rows = db.execute(
                "SELECT * FROM episodes WHERE family=? AND policy_key=? ORDER BY rowid DESC LIMIT 1000",
                (family, policy_key),
            ).fetchall()
        matched = []
        for row in rows:
            try:
                value = self.decode(row)
            except (ValueError, OSError, KeyError):
                continue  # Changed evidence is never supplied as a trusted memory.
            if set(value["defect_kinds"]) & set(defect_kinds):
                matched.append(value)
                if len(matched) == limit:
                    break
        return matched

    def failed_actions(
        self, scene_id: str, candidate_sha256: str, *, family: str, policy_key: str
    ) -> set[str]:
        with self.database.transaction() as db:
            rows = db.execute(
                "SELECT * FROM episodes WHERE scene_id=? AND candidate_sha256=? AND family=? AND policy_key=? AND outcome!=?",
                (scene_id, candidate_sha256, family, policy_key, "ACCEPTED"),
            ).fetchall()
        result = set()
        for row in rows:
            try:
                result.add(self.decode(row)["action_key"])
            except (ValueError, OSError, KeyError):
                continue
        return result
