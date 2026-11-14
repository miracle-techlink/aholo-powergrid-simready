import copy

import pytest

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.evaluation.cup_robustness import inspect_parameters
from simready.simulation.cup_randomization import prepare_episode, sample_protocol
from tests.fixtures.cup_assets import PROTOCOL, fixture_assets


def test_samples_are_order_independent_and_splits_do_not_overlap():
    values = sample_protocol(PROTOCOL)
    changed = copy.deepcopy(PROTOCOL)
    changed["episodes"].reverse()
    changed["parameters"] = dict(reversed(list(changed["parameters"].items())))
    assert {x["id"]: x for x in values} == {
        x["id"]: x for x in sample_protocol(changed)
    }
    assert values[0]["parameters"] != values[1]["parameters"]
    changed["episodes"][0]["seed"] = changed["episodes"][-1]["seed"]
    with pytest.raises(ValueError, match="unique"):
        sample_protocol(changed)


@pytest.mark.parametrize("bad", [True, float("nan"), -1, 10])
def test_invalid_mass_ranges_are_rejected(bad):
    value = copy.deepcopy(PROTOCOL)
    value["parameters"]["cup_mass_kg"]["low"] = bad
    with pytest.raises(ValueError):
        sample_protocol(value)


@pytest.mark.parametrize("explicit", [True, False])
def test_composed_mass_inertia_bindings_and_source_are_checked(tmp_path, explicit):
    from pxr import Usd, UsdPhysics

    assets = fixture_assets(tmp_path, explicit)
    before = digest(assets / "task.usda")
    sample = sample_protocol(PROTOCOL)[0]
    output = tmp_path / "episode"
    prepare_episode(assets, output, sample, object_digest(PROTOCOL))
    report = inspect_parameters(output, sample)
    assert report["status"] == "PASS", report
    assert digest(assets / "task.usda") == before
    assert digest(assets / "plan.json") == digest(output / "plan.json")
    stage = Usd.Stage.Open(str(output / "task.usda"))
    UsdPhysics.MassAPI(stage.GetPrimAtPath("/World/Cup")).CreateMassAttr(0.9)
    stage.GetRootLayer().Save()
    del stage
    changed = inspect_parameters(output, sample)
    assert not changed["checks"]["mass_matches"]
    assert not changed["checks"]["asset_hashes_match"]
    assert changed["status"] == "FAILED"


def test_different_frozen_sample_cannot_validate(tmp_path):
    assets = fixture_assets(tmp_path, True)
    samples = sample_protocol(PROTOCOL)
    output = tmp_path / "episode"
    prepare_episode(assets, output, samples[0], object_digest(PROTOCOL))
    assert inspect_parameters(output, samples[1])["status"] == "FAILED"


def test_missing_asset_hash_is_not_a_complete_randomization_receipt(tmp_path):
    assets = fixture_assets(tmp_path, True)
    sample = sample_protocol(PROTOCOL)[0]
    output = tmp_path / "episode"
    prepare_episode(assets, output, sample, object_digest(PROTOCOL))
    receipt = read_json(output / "randomization.json")
    del receipt["episode_assets_sha256"]["cup.usdc"]
    write_json(output / "randomization.json", receipt)
    with pytest.raises(ValueError, match="complete task asset inventory"):
        inspect_parameters(output, sample)


def test_receipt_must_match_external_frozen_protocol_and_assets(tmp_path):
    assets = fixture_assets(tmp_path, True)
    sample = sample_protocol(PROTOCOL)[0]
    output = tmp_path / "episode"
    receipt = prepare_episode(assets, output, sample, object_digest(PROTOCOL))
    report = inspect_parameters(
        output,
        sample,
        source_assets_sha256=receipt["source_assets_sha256"],
        protocol_sha256=object_digest(PROTOCOL),
    )
    assert report["status"] == "PASS"
    report = inspect_parameters(
        output,
        sample,
        source_assets_sha256={"plan.json": "different"},
        protocol_sha256="different",
    )
    assert report["checks"]["frozen_source_assets_match"] is False
    assert report["checks"]["frozen_protocol_matches"] is False


def test_campaign_rejects_source_mutation_instead_of_adopting_new_baseline(
    tmp_path, monkeypatch
):
    from simready.workflows import cup_robustness

    assets = fixture_assets(tmp_path, True)
    protocol = tmp_path / "protocol.json"
    write_json(protocol, PROTOCOL)
    original = cup_robustness.prepare_episode

    def mutate_source(*args):
        result = original(*args)
        (assets / "plan.json").write_text('{"changed": true}')
        return result

    monkeypatch.setattr(cup_robustness, "prepare_episode", mutate_source)
    report = cup_robustness.run_campaign(
        protocol, assets, tmp_path / "campaign", prepare_only=True
    )
    assert report["status"] == "INCOMPLETE"
    assert len(report["episodes"]) == len(PROTOCOL["episodes"])
    assert all(row["status"] == "FAILED" for row in report["episodes"])
    assert all("Frozen campaign" in row["error"] for row in report["episodes"])
