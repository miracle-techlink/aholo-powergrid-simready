"""Versioned repair recipes distilled from verified episodes, with scoped reuse."""

import json

from simready.core.artifacts import object_digest
from simready.core.types import JsonObject
from simready.experience.episodes import EpisodeStore


class SkillLibrary:
    """Version, validate, promote, retrieve and retire repair recipes."""

    def __init__(self, episodes: EpisodeStore) -> None:
        self.episodes = episodes

    def distill(self, episode_id: str) -> JsonObject | None:
        episode = self.episodes.get(episode_id)
        if (
            episode["outcome"] != "ACCEPTED"
            or not episode["comparison"]["candidate_passed"]
        ):
            return None
        scope = {
            k: episode[k] for k in ("family", "policy_key", "plugin_id", "defect_kinds")
        }
        scope["defect_kinds"] = sorted(set(scope["defect_kinds"]))
        recipe = dict(
            scope,
            plugin_version=episode["plugin_version"],
            parameters=episode["parameters"],
        )
        scope_id, skill_id = object_digest(scope), object_digest(recipe)
        with self.episodes.database.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchone()
            if row is None:
                version = db.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM skills WHERE scope_id=?",
                    (scope_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO skills VALUES (?,?,?,?,?,?,?)",
                    (
                        skill_id,
                        scope_id,
                        version,
                        recipe["family"],
                        recipe["policy_key"],
                        "candidate",
                        json.dumps(recipe, sort_keys=True),
                    ),
                )
            db.execute(
                "INSERT OR IGNORE INTO skill_evidence VALUES (?,?)",
                (skill_id, episode_id),
            )
        return self.get(skill_id)

    def get(self, skill_id: str) -> JsonObject:
        with self.episodes.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchone()
            evidence = db.execute(
                "SELECT episode_id FROM skill_evidence WHERE skill_id=? ORDER BY episode_id",
                (skill_id,),
            ).fetchall()
        if row is None:
            raise ValueError("Unknown skill")
        payload = json.loads(row["payload"])
        if object_digest(payload) != skill_id:
            raise ValueError("Skill payload identity changed")
        return dict(
            payload,
            skill_id=skill_id,
            version=row["version"],
            status=row["status"],
            episode_ids=[r[0] for r in evidence],
        )

    def eligible(self, skill: JsonObject) -> bool:
        episodes = [self.episodes.get(key) for key in skill["episode_ids"]]
        good = [
            e
            for e in episodes
            if e["outcome"] == "ACCEPTED"
            and e["comparison"]["candidate_passed"]
            and e.get("source_images_sha256")
            and all(
                e[key] == skill[key]
                for key in (
                    "family",
                    "policy_key",
                    "plugin_id",
                    "plugin_version",
                    "parameters",
                )
            )
            and sorted(set(e["defect_kinds"])) == skill["defect_kinds"]
        ]
        # Renaming a scene or replaying the same reference image cannot count as
        # independent validation. Content diversity does not prove capture origin.
        return any(
            a["role"] == "validation"
            and a["scene_id"] != b["scene_id"]
            and not set(a["source_images_sha256"]) & set(b["source_images_sha256"])
            for a in good
            for b in good
        )

    def promote(self, skill_id: str) -> JsonObject:
        skill = self.get(skill_id)
        if skill["status"] == "retired":
            raise ValueError("Retired skill cannot be silently reactivated")
        if not self.eligible(skill):
            raise ValueError(
                "Skill needs two distinct passing scenes with disjoint reference images, including a validation campaign"
            )
        with self.episodes.database.transaction() as db:
            changed = db.execute(
                "UPDATE skills SET status=? WHERE skill_id=? AND status!=?",
                ("active", skill_id, "retired"),
            )
            if changed.rowcount != 1:
                raise ValueError("Skill was retired concurrently")
        return self.get(skill_id)

    def retire(self, skill_id: str) -> JsonObject:
        self.get(skill_id)
        with self.episodes.database.transaction() as db:
            db.execute(
                "UPDATE skills SET status=? WHERE skill_id=?", ("retired", skill_id)
            )
        return self.get(skill_id)

    def retrieve(
        self,
        family: str,
        policy_key: str,
        defect_kinds: list[str],
        plugin_versions: dict[str, str],
    ) -> list[JsonObject]:
        with self.episodes.database.transaction() as db:
            ids = db.execute(
                "SELECT skill_id FROM skills WHERE family=? AND policy_key=? AND status=? ORDER BY version DESC",
                (family, policy_key, "active"),
            ).fetchall()
        result = []
        for row in ids:
            try:
                skill = self.get(row[0])
                if plugin_versions.get(skill["plugin_id"]) != skill[
                    "plugin_version"
                ] or not set(skill["defect_kinds"]) & set(defect_kinds):
                    continue
                if self.eligible(skill):
                    result.append(skill)
            except (OSError, ValueError):
                continue
        return result[:16]
