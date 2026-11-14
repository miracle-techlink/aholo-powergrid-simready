"""Reference-content diversity is required before a repair recipe is promoted."""

import copy
import json

import pytest

from simready.core.artifacts import object_digest
from simready.experience import EpisodeStore, SkillLibrary
from tests.fixtures.repair_campaign import campaign


def copy_episode(memory, key):
    value = copy.deepcopy(memory.get(key))
    for field in ("episode_id", "comparison", "outcome"):
        value.pop(field)
    return value


def test_same_evidence_with_new_scene_name_is_not_validation(tmp_path):
    _, _, result = campaign(tmp_path)
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    library = SkillLibrary(memory)
    original = result["rounds"][0]
    renamed = copy_episode(memory, original["episode_id"])
    renamed.update(scene_id="different-label-same-data", role="validation")
    renamed["source_images_sha256"] = ["caller-cannot-invent-a-new-source"]
    stored = memory.append(renamed)
    skill = library.distill(stored["episode_id"])
    assert (
        stored["source_images_sha256"]
        == memory.get(original["episode_id"])["source_images_sha256"]
    )
    assert library.eligible(skill) is False
    with pytest.raises(ValueError, match="disjoint reference"):
        library.promote(skill["skill_id"])


def test_legacy_without_protocol_remains_readable_but_cannot_promote(tmp_path):
    _, _, result = campaign(tmp_path)
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    episode = copy_episode(memory, result["rounds"][0]["episode_id"])
    episode["evidence"].pop("protocol")
    episode.update(scene_id="legacy-scene", role="validation")
    stored = memory.append(episode)
    assert "source_images_sha256" not in memory.get(stored["episode_id"])
    library = SkillLibrary(memory)
    assert not library.eligible(library.distill(stored["episode_id"]))


def test_changed_reference_image_invalidates_stored_episode(tmp_path):
    _, base, result = campaign(tmp_path)
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    (base / "source.png").write_bytes(b"changed after independent verification")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        memory.get(result["rounds"][0]["episode_id"])


def test_legacy_protocol_without_derived_sources_remains_readable(tmp_path):
    _, _, result = campaign(tmp_path)
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    value = memory.get(result["rounds"][0]["episode_id"])
    value.pop("episode_id")
    value.pop("source_images_sha256")
    value["scene_id"] = "legacy-protocol"
    key = object_digest(value)
    # Reproduce the historical payload, bypassing today's append normalization.
    with memory.database.transaction() as db:
        db.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?)",
            (
                key,
                value["family"],
                value["policy_key"],
                value["scene_id"],
                value["candidate_sha256"],
                value["action_key"],
                value["outcome"],
                json.dumps(value, sort_keys=True),
            ),
        )
    stored = memory.get(key)
    assert "protocol" in stored["evidence"]
    assert "source_images_sha256" not in stored
    library = SkillLibrary(memory)
    assert not library.eligible(library.distill(key))


def test_mismatched_recipe_evidence_does_not_enable_promotion(tmp_path):
    _, _, first = campaign(tmp_path)
    _, _, second = campaign(tmp_path, 1, role="validation")
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    library = SkillLibrary(memory)
    other = copy_episode(memory, second["rounds"][0]["episode_id"])
    other["parameters"]["gain"] = 0.125
    stored = memory.append(other)
    recipe = library.distill(stored["episode_id"])
    # A corrupt relation must not lend another recipe's validation evidence.
    with memory.database.transaction() as db:
        db.execute(
            "INSERT INTO skill_evidence VALUES (?, ?)",
            (recipe["skill_id"], first["rounds"][0]["episode_id"]),
        )
    assert library.eligible(library.get(recipe["skill_id"])) is False
