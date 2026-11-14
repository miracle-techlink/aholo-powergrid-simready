import copy
from dataclasses import replace

import pytest

from examples.repair_workflow.run_learning_demo import prepare
from simready.agents.contracts import RepairDecision
from simready.agents.prompting import prompt
from simready.agents.providers.rule_based import RuleBasedProvider
from simready.agents.token_usage import normalize_usage
from simready.core.artifacts import read_json, write_json
from simready.evaluation.regression import compare_scorecards
from simready.experience import EpisodeStore, SkillLibrary
from simready.repair import RepairPluginRegistry
from simready.workflows import run_repair_workflow
from tests.fixtures.repair_campaign import campaign


def test_learning_validation_and_reuse_across_scenes(tmp_path):
    _, _, first = campaign(tmp_path)
    skill = first["rounds"][0]["skill"]
    assert first["status"] == "DEVELOPMENT_PASS" and skill["status"] == "candidate"
    catalog = SkillLibrary(EpisodeStore(tmp_path / "experience.sqlite"))
    with pytest.raises(ValueError, match="two distinct"):
        catalog.promote(skill["skill_id"])
    _, _, second = campaign(tmp_path, 1, role="validation")
    assert second["rounds"][0]["skill"]["status"] == "active"
    _, _, third = campaign(tmp_path, 2)
    context = read_json(tmp_path / "run_2/round_000/context.json")
    assert context["memories"] and context["active_skills"]
    selected = read_json(tmp_path / "run_2/round_000/decision.json")["decision"][
        "proposal_id"
    ]
    proposal = next(p for p in context["proposals"] if p["proposal_id"] == selected)
    assert proposal["skill_id"] == skill["skill_id"]
    assert all(
        r["status"] == "DEVELOPMENT_PASS" and r["release_authorized"] is False
        for r in (first, second, third)
    )
    catalog.retire(skill["skill_id"])
    assert (
        catalog.retrieve(
            skill["family"],
            skill["policy_key"],
            skill["defect_kinds"],
            {skill["plugin_id"]: skill["plugin_version"]},
        )
        == []
    )
    with pytest.raises(ValueError, match="Retired"):
        catalog.promote(skill["skill_id"])


def test_failed_action_remembered_without_repeating_it(tmp_path):
    config, base = prepare(tmp_path, 0)
    path = base / "registry.json"
    registry = read_json(path)
    registry["plugins"][0]["variants"][0]["parameters"]["gain"] = 0.1
    write_json(path, registry)
    result = run_repair_workflow(config, base_dir=base, output_dir=tmp_path / "first")
    assert result["status"] == "NEEDS_EVIDENCE_OR_NEW_STRATEGY"
    assert len(result["rounds"]) == 1 and result["rounds"][0]["reason"] == "REGRESSION"
    assert result["candidate"] == str(base / "candidate.json")
    second = run_repair_workflow(config, base_dir=base, output_dir=tmp_path / "second")
    assert second["agent_calls"] == 0 and second["rounds"] == []
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    episode = memory.get(result["rounds"][0]["episode_id"])
    assert episode["outcome"] == "REJECTED"
    assert SkillLibrary(memory).distill(episode["episode_id"]) is None


def test_memory_evidence_tamper_and_plugin_version_invalidation(tmp_path):
    _, _, first = campaign(tmp_path)
    _, base, _ = campaign(tmp_path, 1, role="validation")
    skill = first["rounds"][0]["skill"]
    catalog = SkillLibrary(EpisodeStore(tmp_path / "experience.sqlite"))
    assert (
        catalog.retrieve(
            skill["family"],
            skill["policy_key"],
            skill["defect_kinds"],
            {"fixture_gain": "different"},
        )
        == []
    )
    path = tmp_path / "run_0/round_000/verification/scorecard.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="evidence"):
        catalog.episodes.get(first["rounds"][0]["episode_id"])
    assert (
        catalog.retrieve(
            skill["family"],
            skill["policy_key"],
            skill["defect_kinds"],
            {skill["plugin_id"]: skill["plugin_version"]},
        )
        == []
    )


@pytest.mark.parametrize("kind", ["phase", "role", "budget"])
def test_invalid_campaigns_rejected_before_agent(tmp_path, kind):
    config, base = prepare(tmp_path, 0)
    if kind == "phase":
        path = base / "protocol.json"
        value = read_json(path)
        value["phase"] = "sealed_release"
        write_json(path, value)
    elif kind == "role":
        config["role"] = "sealed_release"
    else:
        config["budget"]["max_rounds"] = True
    with pytest.raises(ValueError):
        run_repair_workflow(config, base_dir=base, output_dir=tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_missing_usage_stops_before_executing_plugin(tmp_path):
    class UnknownUsage(RuleBasedProvider):
        def decide(self, *args, **kwargs):
            return replace(super().decide(*args, **kwargs), usage=None)

    config, base = prepare(tmp_path, 0)
    result = run_repair_workflow(
        config,
        base_dir=base,
        output_dir=tmp_path / "run",
        decision_provider=UnknownUsage(),
    )
    assert result["status"] == "USAGE_UNKNOWN" and result["rounds"] == []
    assert not (tmp_path / "run/round_000/execution").exists()


def test_token_budget_checked_before_plugin_execution(tmp_path):
    class Expensive(RuleBasedProvider):
        def decide(self, *args, **kwargs):
            return replace(
                super().decide(*args, **kwargs),
                usage={
                    "input_tokens": 50,
                    "output_tokens": 50,
                    "total_tokens": 100,
                    "cached_input_tokens": 0,
                },
            )

    config, base = prepare(tmp_path, 0)
    config["budget"]["max_total_tokens"] = 10
    result = run_repair_workflow(
        config,
        base_dir=base,
        output_dir=tmp_path / "run",
        decision_provider=Expensive(),
    )
    assert result["status"] == "BUDGET_EXHAUSTED" and result["rounds"] == []


def test_plugin_process_failure_is_recorded(tmp_path):
    config, base = prepare(tmp_path, 0)
    path = base / "registry.json"
    registry = read_json(path)
    registry["plugins"][0]["tasks"][0]["argv"] = [
        "{python}",
        "-c",
        "raise SystemExit(8)",
    ]
    write_json(path, registry)
    result = run_repair_workflow(config, base_dir=base, output_dir=tmp_path / "run")
    episode = EpisodeStore(tmp_path / "experience.sqlite").get(
        result["rounds"][0]["episode_id"]
    )
    assert episode["outcome"] == "ERROR"
    assert (
        result["status"] == "NEEDS_EVIDENCE_OR_NEW_STRATEGY"
        and result["agent_calls"] == 1
    )


def test_fixture_plugin_cannot_run_on_real_reference(tmp_path):
    _, base = prepare(tmp_path, 0)
    registry = RepairPluginRegistry(base / "registry.json")
    protocol = read_json(base / "protocol.json")
    protocol["views"][0]["reference_kind"] = "real_video"
    assert (
        registry.propose_repairs(
            protocol,
            "synthetic-gain",
            [
                {
                    "kind": "appearance_mismatch",
                    "repairable_now": True,
                    "asset_ids": ["scene"],
                }
            ],
            [],
            set(),
        )
        == []
    )


def test_plugin_implementation_changes_invalidate_version(tmp_path):
    _, base = prepare(tmp_path, 0)
    path = base / "registry.json"
    value = read_json(path)
    worker = base / "worker.py"
    worker.write_text("# v1")
    value["plugins"][0]["tasks"][0]["inputs"]["worker"] = str(worker)
    write_json(path, value)
    before = RepairPluginRegistry(path).versions
    worker.write_text("# v2")
    assert RepairPluginRegistry(path).versions != before


def test_decision_rejects_arbitrary_actions_and_unknown_proposals():
    context = {"proposals": [{"proposal_id": "known"}]}
    for value in [
        dict(action="shell", proposal_id=None, reason="x"),
        dict(action="repair", proposal_id="invented", reason="x"),
        dict(action="stop", proposal_id="known", reason="x"),
    ]:
        with pytest.raises(ValueError):
            RepairDecision.parse(value, context)
    assert (
        RepairDecision.parse(
            dict(action="repair", proposal_id="known", reason="x"), context
        ).proposal_id
        == "known"
    )
    with pytest.raises(ValueError, match="byte limit"):
        prompt({"long": "x" * 1024}, max_bytes=10)
    assert (
        normalize_usage(
            {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 8}
        )
        is None
    )


def test_comparison_rejects_any_required_regression_even_if_aggregate_improves():
    before = {
        "protocol_sha256": "fixed",
        "checks": [
            dict(
                check_id="a",
                required=True,
                metric="a",
                op=">=",
                limit=10,
                value=8,
                status="FAIL",
            ),
            dict(
                check_id="b",
                required=True,
                metric="b",
                op=">=",
                limit=10,
                value=20,
                status="PASS",
            ),
        ],
    }
    after = copy.deepcopy(before)
    after["status"] = "PASS"
    after["checks"][0].update(value=15, status="PASS")
    after["checks"][1]["value"] = 19  # still passes, but is a measurable regression
    assert compare_scorecards(before, after)["accepted"] is False


def test_memory_payload_tamper_is_skipped(tmp_path):
    _, _, first = campaign(tmp_path)
    skill = first["rounds"][0]["skill"]
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    with memory.database.transaction() as db:
        db.execute("UPDATE episodes SET payload=?", ("{}",))
    assert (
        memory.retrieve(skill["family"], skill["policy_key"], skill["defect_kinds"])
        == []
    )
    with pytest.raises(ValueError, match="identity"):
        memory.get(first["rounds"][0]["episode_id"])


def test_concurrent_memory_appends_are_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _, _, first = campaign(tmp_path)
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    episode = memory.get(first["rounds"][0]["episode_id"])
    episode.pop("episode_id")
    episode.pop("comparison")
    episode.pop("outcome")
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(lambda _: memory.append(episode), range(12)))
    assert len({e["episode_id"] for e in copies}) == 1
    with memory.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


def test_skill_payload_tamper_is_skipped(tmp_path):
    _, _, first = campaign(tmp_path)
    campaign(tmp_path, 1, role="validation")
    skill = first["rounds"][0]["skill"]
    memory = EpisodeStore(tmp_path / "experience.sqlite")
    with memory.database.transaction() as db:
        db.execute("UPDATE skills SET payload=?", ("{}",))
    assert (
        SkillLibrary(memory).retrieve(
            skill["family"],
            skill["policy_key"],
            skill["defect_kinds"],
            {skill["plugin_id"]: skill["plugin_version"]},
        )
        == []
    )
