"""Generic consistency faults across synthetic industrial, warehouse and room cases."""

import copy
import math

import pytest
from test_unified_benchmark import fixture

from simready_bridge.artifacts import digest, write_json
from simready_bridge.benchmark.asset_consistency import asset_metrics
from simready_bridge.benchmark.contracts import read_json, reference
from simready_bridge.benchmark.evaluator import evaluate


def graph(family="warehouse"):
    obj, part, action = {
        "warehouse": ("rack", "shelf", "supporting"),
        "room": ("desk", "drawer", "slidable"),
        "industrial": ("pipe", "valve", "rotatable"),
    }[family]

    def asset(key, category, level, parent, affordances):
        return dict(
            asset_id=key,
            category=category,
            level=level,
            parent_id=parent,
            center_m=[0.0, 0.0, 1.0],
            size_m=[1.0, 0.5, 0.3],
            rotation_xyzw=[0.0, 0.0, 0.0, 1.0],
            material_class="metal",
            state="static" if level == "structure" else "closed",
            affordances=affordances,
            critical=key == "part",
        )

    return dict(
        schema_version=1,
        scene_id=family,
        scope_id="observed_zone_1",
        coordinate_frame="registered_world",
        units="m",
        inventory_complete=True,
        assets=[
            asset("floor", "floor", "structure", None, []),
            asset("object", obj, "object", None, []),
            asset("part", part, "part", "object", [action]),
        ],
        relations=[dict(subject="object", predicate="supported_by", object="floor")],
    )


@pytest.mark.parametrize("family", ["warehouse", "room", "industrial"])
def test_common_rules_apply_to_non_button_assets(family):
    ref = graph(family)
    result = asset_metrics(ref, copy.deepcopy(ref))
    assert (
        result["metrics"]["missing_count"]
        == result["metrics"]["relation_error_count"]
        == 0
    )
    assert result["metrics"]["position_error_max_m"] == 0
    assert result["details"] == []


def test_missing_shelf_and_extra_object():
    ref = graph()
    pred = copy.deepcopy(ref)
    pred["assets"][2]["asset_id"] = "invented"
    result = asset_metrics(ref, pred)
    assert (
        result["metrics"]["missing_count"]
        == result["metrics"]["missing_critical_count"]
        == 1
    )
    assert result["metrics"]["extra_count"] == 1


def test_partial_source_does_not_call_unseen_object_hallucinated():
    ref = graph()
    ref["inventory_complete"] = False
    pred = copy.deepcopy(ref)
    item = copy.deepcopy(pred["assets"][1])
    item["asset_id"] = "unobserved"
    pred["assets"].append(item)
    result = asset_metrics(ref, pred)
    assert (
        result["metrics"]["extra_count"] is None
        and result["metrics"]["unmatched_count"] == 1
    )
    assert any(d["kind"] == "unverified_asset" for d in result["details"])


def test_parent_material_state_affordance_and_relation_faults():
    ref = graph("room")
    pred = copy.deepcopy(ref)
    pred["assets"][2].update(
        parent_id="floor",
        material_class="glass",
        state="open",
        affordances=["rotatable"],
    )
    pred["relations"] = []
    m = asset_metrics(ref, pred)["metrics"]
    for key in [
        "parent_error_count",
        "material_error_count",
        "state_error_count",
        "affordance_error_count",
        "relation_error_count",
    ]:
        assert m[key] == 1


def test_geometry_scale_and_orientation_are_not_pixel_centres():
    ref = graph("industrial")
    pred = copy.deepcopy(ref)
    pred["assets"][2].update(
        center_m=[0.03, 0.04, 1.0],
        size_m=[2.0, 0.5, 0.3],
        rotation_xyzw=[0, 0, math.sqrt(0.5), math.sqrt(0.5)],
    )
    m = asset_metrics(ref, pred)["metrics"]
    assert m["position_error_max_m"] == pytest.approx(0.05)
    assert m["size_relative_error_max"] == 1
    assert m["orientation_error_max_deg"] == pytest.approx(90)
    pred = copy.deepcopy(ref)
    pred["assets"][2]["rotation_xyzw"] = [0, 0, 0, -1]
    assert asset_metrics(ref, pred)["metrics"]["orientation_error_max_deg"] == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("scene_id", "other-room"),
        ("scope_id", "unregistered"),
        ("coordinate_frame", "other-world"),
        ("units", "cm"),
    ],
)
def test_different_scene_or_coordinates_require_registration(key, value):
    ref = graph()
    pred = copy.deepcopy(ref)
    pred[key] = value
    with pytest.raises(ValueError):
        asset_metrics(ref, pred)


def test_unknown_reference_and_missing_prediction_are_separate():
    ref = graph()
    pred = copy.deepcopy(ref)
    del ref["assets"][2]["material_class"]
    del pred["assets"][1]["size_m"]
    policy = {
        "required_fields_by_level": {"part": ["material_class"]},
        "relations_required": True,
    }
    result = asset_metrics(ref, pred, policy)
    assert result["metrics"]["unknown_source_property_count"] == 1
    assert result["metrics"]["missing_property_count"] == 1


def test_missing_geometry_has_no_zero_error_default():
    ref = graph()
    pred = copy.deepcopy(ref)
    for row in pred["assets"]:
        del row["center_m"]
    m = asset_metrics(ref, pred)["metrics"]
    assert m["position_error_max_m"] is None and m["missing_property_count"] == 3


def test_invalid_and_duplicate_graph_data_rejected():
    ref = graph()
    pred = copy.deepcopy(ref)
    pred["assets"] *= 2
    with pytest.raises(ValueError):
        asset_metrics(ref, pred)
    pred = copy.deepcopy(ref)
    pred["assets"][1]["size_m"] = [-1, 1, 1]
    with pytest.raises(ValueError):
        asset_metrics(ref, pred)


def test_asset_failure_is_localized_and_cannot_hide_behind_clean_pixels(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
    ref = graph("room")
    pred = copy.deepcopy(ref)
    pred["assets"][2]["parent_id"] = "floor"
    write_json(tmp_path / "reference_graph.json", ref)
    write_json(tmp_path / "candidate_graph.json", pred)
    protocol["asset_graph"] = reference(tmp_path / "reference_graph.json")
    protocol["checks"].append(
        dict(
            check_id="parents",
            track="instances",
            metric="assets.parent_error_count",
            op="==",
            limit=0,
            required=True,
            severity="critical",
            calibration="structural",
            defect_kind="wrong_asset_parent",
        )
    )
    write_json(p, protocol)
    candidate["protocol_sha256"] = digest(p)
    candidate["asset_graph"] = reference(tmp_path / "candidate_graph.json")
    write_json(c, candidate)
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "FAIL"
    defects = read_json(tmp_path / "out/defects.json")["defects"]
    assert (
        defects[0]["asset_ids"] == ["part"]
        and defects[0]["kind"] == "wrong_asset_parent"
    )
    assert result["tracks"]["appearance"] == "PASS"


def test_missing_graph_is_incomplete_not_empty_success(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
    write_json(tmp_path / "reference_graph.json", graph())
    protocol["asset_graph"] = reference(tmp_path / "reference_graph.json")
    protocol["checks"].append(
        dict(
            check_id="assets",
            track="instances",
            metric="assets.missing_count",
            op="==",
            limit=0,
            required=True,
            severity="critical",
            calibration="structural",
            defect_kind="missing_asset",
        )
    )
    write_json(p, protocol)
    candidate["protocol_sha256"] = digest(p)
    write_json(c, candidate)
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "INCOMPLETE"
    assert "assets" in result["missing_checks"]
