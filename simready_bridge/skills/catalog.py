"""Versioned repair recipes distilled from verified episodes, with scoped reuse."""

import json

from ..artifacts import object_digest


class SkillCatalog:
    def __init__(self, memory):
        self.memory = memory

    def distill(self, episode_id):
        episode = self.memory.get(episode_id)
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
        with self.memory.connect() as db:
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

    def get(self, skill_id):
        with self.memory.connect() as db:
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

    def eligible(self, skill):
        episodes = [self.memory.get(key) for key in skill["episode_ids"]]
        good = [
            e
            for e in episodes
            if e["outcome"] == "ACCEPTED" and e["comparison"]["candidate_passed"]
        ]
        # Distinct scenes and a separately declared regression run. No sealed
        # feedback is exposed and this does not confer scene release authority.
        return len({e["scene_id"] for e in good}) >= 2 and any(
            e["role"] == "validation" for e in good
        )

    def promote(self, skill_id):
        skill = self.get(skill_id)
        if skill["status"] == "retired":
            raise ValueError("Retired skill cannot be silently reactivated")
        if not self.eligible(skill):
            raise ValueError(
                "Skill needs two distinct passing scenes, including a validation campaign"
            )
        with self.memory.connect() as db:
            changed = db.execute(
                "UPDATE skills SET status=? WHERE skill_id=? AND status!=?",
                ("active", skill_id, "retired"),
            )
            if changed.rowcount != 1:
                raise ValueError("Skill was retired concurrently")
        return self.get(skill_id)

    def retire(self, skill_id):
        self.get(skill_id)
        with self.memory.connect() as db:
            db.execute(
                "UPDATE skills SET status=? WHERE skill_id=?", ("retired", skill_id)
            )
        return self.get(skill_id)

    def retrieve(self, family, policy_key, defect_kinds, plugin_versions):
        with self.memory.connect() as db:
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
