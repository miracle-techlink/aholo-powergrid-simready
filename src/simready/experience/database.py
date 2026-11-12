"""Own the versioned SQLite schema and transaction lifecycle for experience."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ExperienceDatabase:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
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
    def transaction(self):
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
