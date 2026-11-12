"""Adding performance memory must preserve existing scene-repair experience."""

import sqlite3

from simready.experience.database import ExperienceDatabase


def test_version_one_database_keeps_existing_episode(tmp_path):
    path = tmp_path / "memory.sqlite"
    database = ExperienceDatabase(path)
    with database.transaction() as db:
        db.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?)",
            ("id", "family", "policy", "scene", "candidate", "action", "ERROR", "{}"),
        )
        db.execute("PRAGMA user_version=1")
    upgraded = ExperienceDatabase(path)
    with upgraded.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT episode_id FROM episodes").fetchone()[0] == "id"
        assert (
            db.execute("SELECT count(*) FROM performance_experiments").fetchone()[0]
            == 0
        )
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
