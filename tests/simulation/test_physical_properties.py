"""Contract rejection and composed USD read-back for heterogeneous assets."""

import copy
import json
import struct

import numpy as np
import pytest

from simready.simulation.physical_spec import normalize_physics
from simready.simulation.scene_preparation import prepare_scene


def normalized(value=None, *, metric=True, geometry="box", **kwargs):
    return normalize_physics(
        value,
        geometry_type=geometry,
        fallback_contact={
            "static_friction": 0.8,
            "dynamic_friction": 0.7,
            "restitution": 0,
        },
        metric_scale_declared=metric,
        **kwargs,
    )


def source_record(unit, kind="category_prior", interval=None):
    return {
        "source_kind": kind,
        "source": "Synthetic fixture assumption, not a measurement",
        "unit": unit,
        "interval": interval,
    }


def scene_spec():
    objects = []
    for index, category in enumerate(
        ["cabinet", "door", "table", "chair", "ground", "machine"]
    ):
        coefficient = 0.3 + index * 0.1
        objects.append(
            {
                "id": category.title(),
                "type": "box",
                "category": category,
                "bounds_world": {"min": [index, 0, 0], "max": [index + 0.8, 1, 2]},
                "physics": {
                    "contact": {
                        "static_friction": coefficient,
                        "dynamic_friction": coefficient / 2,
                        "restitution": index * 0.05,
                    },
                    "provenance": {
                        "contact.static_friction": source_record("1", interval=[0.1, 1])
                    },
                },
            }
        )
    objects[0]["visible"] = False
    objects[1]["physics"]["motion_type"] = "kinematic"
    objects[3]["physics"].update(
        motion_type="dynamic",
        mass_kg=2.0,
        center_of_mass_m=[0.1, 0, 0.2],
        inertia_kg_m2=[0.2, 0.3, 0.4],
    )
    objects.append(
        {
            "id": "Button",
            "type": "button",
            "category": "push_button",
            "position_world": [2, 0, 1],
            "axis": "Y",
            "physics": {
                "mass_kg": 0.04,
                "contact": {"static_friction": 0.9, "dynamic_friction": 0.6},
            },
        }
    )
    return {
        "schema_version": 1,
        "source_to_world": np.eye(4).tolist(),
        "scale_provenance": {
            "status": "verified",
            "method": "synthetic metres",
            "evidence": "fixture dimensions",
        },
        "floor_plane": {"normal": [0, 0, 1], "offset": 0},
        "objects": objects,
    }


def input_ply(path):
    # Tiny RGB fixture; binary layout is independent of the production encoder.
    points = [(0, 0, 0), (6, 0, 0), (6, 2, 0), (0, 2, 0), (1, 1, 2)]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 5\n"
    header += "".join(f"property float {name}\n" for name in "xyz")
    header += "".join(f"property uchar {name}\n" for name in ["red", "green", "blue"])
    path.write_bytes(
        (header + "end_header\n").encode()
        + b"".join(struct.pack("<fffBBB", *point, 100, 120, 140) for point in points)
    )
    return path


def test_static_defaults_have_per_parameter_sources_and_no_invented_mass():
    value = normalized(metric=False)
    assert value["motion_type"] == "static"
    assert "mass_kg" not in value
    assert value["provenance"]["contact.static_friction"]["source_kind"] == "default"
    assert value["validation"]["scale_status"] == "unverified"
    assert not value["validation"]["independently_verified"]


@pytest.mark.parametrize(
    "bad",
    [
        {"unknown": 1},
        {"motion_type": "moving"},
        {"motion_type": []},
        {"motion_type": "dynamic"},
        {"mass_kg": -1},
        {"mass_kg": True},
        {"mass_kg": float("nan")},
        {"contact": {"dynamic_friction": 0.9}},
        {"contact": {"restitution": 1.1}},
        {"contact": {"friction": 0.3}},
        {"contact": None},
        {"mass_kg": 2, "inertia_kg_m2": [1, 1, 3]},
        {"mass_kg": 2, "inertia_kg_m2": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
        {"center_of_mass_m": [0, 0, 0]},
        {"validation": {"independently_verified": True}},
        {"provenance": {"mass_kg": source_record("kg")}},
        {"provenance": {"contact.static_friction": source_record("kg")}},
        {
            "provenance": {
                "contact.static_friction": source_record("1", interval=[0.9, 1])
            }
        },
        {
            "provenance": {
                "contact.static_friction": source_record("1", interval=[True, 1])
            }
        },
        {
            "provenance": {
                "contact.static_friction": source_record("1", interval=[-0.1, 1])
            }
        },
        {"provenance": {"contact.static_friction": {"source_kind": "measured"}}},
    ],
)
def test_malformed_declarations_rejected(bad):
    with pytest.raises(ValueError):
        normalized(bad)


@pytest.mark.parametrize("motion", ["dynamic", "kinematic"])
def test_new_moving_body_requires_metric_scale(motion):
    with pytest.raises(ValueError, match="metric scale"):
        normalized({"motion_type": motion, "mass_kg": 2}, metric=False)


def test_legacy_button_keeps_dynamic_cap_without_claiming_metric_scale():
    value = normalized(geometry="button", metric=False, legacy_mass_kg=0.02)
    assert value["motion_type"] == "dynamic"
    assert value["mass_kg"] == 0.02
    assert value["validation"]["scale_status"] == "unverified"
    with pytest.raises(ValueError, match="metric scale"):
        normalized(
            {"mass_kg": 0.03}, geometry="button", metric=False, legacy_mass_kg=0.02
        )
    with pytest.raises(ValueError, match="conflict"):
        normalized(
            {"mass_kg": 0.03},
            geometry="button",
            legacy_mass_kg=0.02,
            legacy_mass_explicit=True,
        )


def test_measured_label_is_a_declaration_not_an_independent_validation():
    value = normalized(
        {
            "mass_kg": 2,
            "provenance": {"mass_kg": source_record("kg", "measured", [1.9, 2.1])},
        }
    )
    assert value["provenance"]["mass_kg"]["source_kind"] == "measured"
    assert not value["validation"]["real_world_validated"]
    assert value["validation"]["status"] == "unvalidated"


def test_vector_provenance_uses_scalar_components_and_fixed_si_units():
    value = normalized(
        {
            "mass_kg": 2,
            "center_of_mass_m": [0.1, 0, 0.2],
            "inertia_kg_m2": [0.2, 0.3, 0.4],
            "provenance": {
                "center_of_mass_m.x": source_record("m", "catalog", [0, 0.2]),
                "inertia_kg_m2.z": source_record("kg*m^2", "derived", [0.3, 0.5]),
            },
        }
    )
    assert value["provenance"]["center_of_mass_m.x"]["interval"] == [0, 0.2]
    assert value["provenance"]["inertia_kg_m2.z"]["source_kind"] == "derived"
    assert value["provenance"]["inertia_kg_m2.x"]["unit"] == "kg*m^2"


def test_multiclass_roundtrip_independent_bindings_motion_mass_and_metadata(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    spec = scene_spec()
    original = copy.deepcopy(spec)
    output = tmp_path / "prepared"
    prepare_scene(input_ply(tmp_path / "input.ply"), output, spec)
    assert spec == original  # Normalization must not rewrite callers' evidence.
    catalog = json.loads((output / "asset_catalog.json").read_text())
    stage = Usd.Stage.Open(str(output / "scene.usda"))
    materials = set()
    for source, obj in zip(spec["objects"], catalog["objects"], strict=True):
        root_path = "/World/Objects/" + obj["id"]
        root = stage.GetPrimAtPath(root_path)
        assert (
            root.GetCustomDataByKey("simready:semantic_category") == source["category"]
        )
        assert root.GetCustomDataByKey("simready:geometry_type") == source["type"]
        assert (
            json.loads(root.GetCustomDataByKey("simready:physics_json"))
            == obj["physics"]
        )
        assert obj["visible"] == source.get("visible", True)
        for collider_path in obj["physics_binding"]["collision_prims"]:
            collider = stage.GetPrimAtPath(collider_path)
            assert collider.HasAPI(UsdPhysics.CollisionAPI)
            material, _ = UsdShade.MaterialBindingAPI(collider).ComputeBoundMaterial(
                "physics"
            )
            assert str(material.GetPath()) == obj["physics_binding"]["material_prim"]
            materials.add(str(material.GetPath()))
            material_api = UsdPhysics.MaterialAPI(material.GetPrim())
            assert material_api.GetStaticFrictionAttr().Get() == pytest.approx(
                source["physics"]["contact"]["static_friction"]
            )
            assert material_api.GetDynamicFrictionAttr().Get() == pytest.approx(
                source["physics"]["contact"]["dynamic_friction"]
            )
            assert UsdGeom.Imageable(collider).ComputeVisibility() == (
                "inherited" if obj["visible"] else "invisible"
            )
        if obj["type"] == "box":
            moving = obj["physics"]["motion_type"] != "static"
            assert root.HasAPI(UsdPhysics.RigidBodyAPI) == moving
            if moving:
                assert UsdPhysics.RigidBodyAPI(
                    root
                ).GetKinematicEnabledAttr().Get() == (obj["id"] == "Door")
            # Root body coordinates are unscaled metres even for non-unit proxies.
            bbox = (
                UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(),
                    [UsdGeom.Tokens.default_],
                    False,
                    True,
                )
                .ComputeWorldBound(root)
                .ComputeAlignedRange()
            )
            np.testing.assert_allclose(
                bbox.GetMin(), source["bounds_world"]["min"], atol=1e-6
            )
            np.testing.assert_allclose(
                bbox.GetMax(), source["bounds_world"]["max"], atol=1e-6
            )
    assert len(materials) == len(spec["objects"])
    mass = UsdPhysics.MassAPI(stage.GetPrimAtPath("/World/Objects/Chair"))
    assert mass.GetMassAttr().Get() == 2
    np.testing.assert_allclose(mass.GetCenterOfMassAttr().Get(), [0.1, 0, 0.2])
    np.testing.assert_allclose(mass.GetDiagonalInertiaAttr().Get(), [0.2, 0.3, 0.4])
    assert mass.GetPrincipalAxesAttr().Get().GetReal() == 1
    cap = stage.GetPrimAtPath("/World/Objects/Button/Cap")
    assert UsdPhysics.MassAPI(cap).GetMassAttr().Get() == pytest.approx(0.04)
    assert not UsdPhysics.RigidBodyAPI(cap).GetKinematicEnabledAttr().Get()
    assert (
        UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath("/World/Objects/Button/Base"))
        .GetKinematicEnabledAttr()
        .Get()
    )
    joint = UsdPhysics.PrismaticJoint(
        stage.GetPrimAtPath("/World/Objects/Button/Joint")
    )
    assert joint.GetLowerLimitAttr().Get() == pytest.approx(-0.004)
    assert UsdPhysics.DriveAPI(joint, "linear").GetStiffnessAttr().Get() == 400


@pytest.mark.parametrize(
    "bad",
    [{"visible": "false"}, {"physics": None}, {"physics": {"motion_type": "dynamic"}}],
)
def test_invalid_object_fields_fail_before_creating_output(tmp_path, bad):
    pytest.importorskip("pxr")
    spec = scene_spec()
    spec["objects"][0].update(bad)
    output = tmp_path / "prepared"
    with pytest.raises(ValueError):
        prepare_scene(input_ply(tmp_path / "input.ply"), output, spec)
    assert not output.exists()
