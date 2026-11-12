import pytest

from simready.core.artifacts import read_json, write_json
from simready.evaluation.scoring import evaluate_candidate
from simready.repair.planning import plan_repairs
from tests.fixtures.scene_candidate import make_scene_candidate
from tests.paths import REPOSITORY_ROOT as ROOT


def test_scheduler_groups_errors_and_never_executes_legacy_commands(tmp_path):
    p, c, _, candidate, _ = make_scene_candidate(tmp_path)
    candidate["views"] = []
    write_json(c, candidate)
    evaluate_candidate(p, c, tmp_path / "out")
    bundle = read_json(tmp_path / "out/defects.json")
    registry = read_json(ROOT / "configs/repair/adapter_catalog.v1.json")
    proposed = plan_repairs(bundle, registry)
    assert proposed["executed"] is False and proposed["blocked"]
    assert all(a["execution_status"] == "ADAPTER_REQUIRED" for a in proposed["actions"])
    assert (
        plan_repairs(bundle, registry, stagnant_rounds=2)["status"]
        == "NEEDS_NEW_STRATEGY"
    )
    assert plan_repairs(bundle, registry, round_index=4)["status"] == "BUDGET_EXHAUSTED"
    bundle["phase"] = "sealed_release"
    with pytest.raises(ValueError):
        plan_repairs(bundle, registry)


def test_scheduler_serializes_conflicting_geometry_writes():
    registry = read_json(ROOT / "configs/repair/adapter_catalog.v1.json")
    bundle = dict(
        phase="development",
        protocol_sha256="p",
        candidate_sha256="c",
        defects=[
            dict(
                defect_id="one",
                kind="surface_hole",
                repairable_now=True,
                asset_ids=["cabinet"],
            ),
            dict(
                defect_id="two",
                kind="missing_instance",
                repairable_now=True,
                asset_ids=["button"],
            ),
        ],
    )
    result = plan_repairs(bundle, registry)
    assert (
        len(result["actions"]) == 1
        and result["deferred"][0]["reason"] == "WRITE_CONFLICT"
    )
    assert "physics" in result["actions"][0]["invalidates"]
