"""Append-only, evidence-bound episode memory. Safe for multiple local writers."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..artifacts import digest, object_digest, read_json
from ..benchmark.comparison import compare


def verify_reference(reference):
    path = Path(reference["path"])
    if not path.is_absolute() or digest(path) != reference["sha256"]:
        raise ValueError("Memory evidence missing or changed")
    return path


class MemoryStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(
                    "Unsupported memory schema; explicit migration required"
                )
            db.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY, family TEXT NOT NULL, policy_key TEXT NOT NULL,
                    scene_id TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
                    action_key TEXT NOT NULL, outcome TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS episode_scope ON episodes(family, policy_key, scene_id);
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, version INTEGER NOT NULL,
                    family TEXT NOT NULL, policy_key TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, UNIQUE(scope_id, version));
                CREATE TABLE IF NOT EXISTS skill_evidence (
                    skill_id TEXT NOT NULL REFERENCES skills(skill_id),
                    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
                    PRIMARY KEY(skill_id, episode_id));
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, episode):
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
        if "after" in evidence:
            after = read_json(evidence["after"]["path"])
            if after.get("phase") != "development":
                raise ValueError("Sealed evidence cannot enter memory")
            value["comparison"] = compare(before, after)
            value["outcome"] = (
                "ACCEPTED" if value["comparison"]["accepted"] else "REJECTED"
            )
        else:
            value["comparison"] = None
            value["outcome"] = "ERROR"
        key = object_digest(value)
        encoded = json.dumps(value, sort_keys=True, allow_nan=False)
        with self.connect() as db:
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
        return dict(value, episode_id=row["episode_id"])

    def get(self, episode_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown episode")
        return self.decode(row)

    def retrieve(self, family, policy_key, defect_kinds, limit=8):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Retrieval limit must be between 1 and 100")
        with self.connect() as db:
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

    def failed_actions(self, scene_id, candidate_sha256, *, family, policy_key):
        with self.connect() as db:
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
