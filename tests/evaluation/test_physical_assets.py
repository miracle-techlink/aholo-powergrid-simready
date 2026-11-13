"""Independent authored stages, adversarial changes and fresh-process readback."""

import copy
import json
import subprocess
import sys

import pytest

from simready.core.artifacts import read_json, write_json
from simready.evaluation.physical_assets import inspect_physical_assets

pytest.importorskip("pxr", reason="Optional USD bindings are required for readback")
from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: E402


def _physics(index, *, motion="static", mass=None):
    contact = {
        "static_friction": 0.4 + index * 0.1,
        "dynamic_friction": 0.25 + index * 0.1,
        "restitution": index * 0.02,
    }
    result = {"motion_type": motion, "contact": contact}
    if mass is not None:
        result["mass_kg"] = mass
    result["provenance"] = {
        **{
            "contact." + key: {
                "source_kind": "user_declared",
                "source": "Synthetic independent test fixture",
                "unit": "1",
                "interval": None,
            }
            for key in contact
        },
        **(
            {
                "mass_kg": {
                    "source_kind": "user_declared",
                    "source": "Synthetic independent test fixture",
                    "unit": "kg",
                    "interval": None,
                }
            }
            if mass is not None
            else {}
        ),
    }
    return result


@pytest.fixture
def package(tmp_path):
    """Author the expected USD directly, without importing production simulation."""
    output = tmp_path / "package"
    output.mkdir()
    objects = []
    for index, name in enumerate(("floor", "cabinet", "table", "chair")):
        motion = (
            "dynamic"
            if name == "chair"
            else "kinematic"
            if name == "table"
            else "static"
        )
        obj = {
            "id": name,
            "type": "box",
            "category": name,
            "bounds_world": {"min": [index * 2, 0, 0], "max": [index * 2 + 1, 1, 1]},
            "physics": _physics(
                index, motion=motion, mass=8 if motion != "static" else None
            ),
        }
        objects.append(obj)
    objects.append(
        {
            "id": "button",
            "type": "button",
            "category": "button",
            "position_world": [2.5, 1, 0.7],
            "axis": "Y",
            "radius": 0.02,
            "cap_height": 0.01,
            "travel": 0.004,
            "stiffness": 400.0,
            "damping": 4.0,
            "max_force": 8.0,
            "physics": _physics(4, motion="dynamic", mass=0.02),
        }
    )
    spec = {
        "schema_version": 1,
        "objects": objects,
        "source_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "scale_provenance": {
            "status": "verified",
            "method": "synthetic fixture",
            "evidence": "synthetic SI coordinates",
        },
    }
    spec_path = tmp_path / "scene_spec.json"
    write_json(spec_path, spec)
    catalog = {"schema_version": 1, "objects": copy.deepcopy(objects)}
    stage = Usd.Stage.CreateNew(str(output / "scene.usda"))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Objects")
    for obj in catalog["objects"]:
        root = "/World/Objects/" + obj["id"]
        body = UsdGeom.Xform.Define(stage, root)
        obj["physics"]["validation"] = {
            "status": "unvalidated",
            "real_world_validated": False,
            "independently_verified": False,
            "scale_status": "operator_declared_metric",
        }
        body.GetPrim().SetCustomDataByKey(
            "simready:physics_json",
            json.dumps(obj["physics"], sort_keys=True, separators=(",", ":")),
        )
        for key, value in (
            ("object_id", obj["id"]),
            ("semantic_category", obj["category"]),
            ("geometry_type", obj["type"]),
        ):
            body.GetPrim().SetCustomDataByKey("simready:" + key, value)
        material_path = root + "/Looks/PhysicsMaterial"
        material = UsdShade.Material.Define(stage, material_path)
        api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        api.CreateStaticFrictionAttr(obj["physics"]["contact"]["static_friction"])
        api.CreateDynamicFrictionAttr(obj["physics"]["contact"]["dynamic_friction"])
        api.CreateRestitutionAttr(obj["physics"]["contact"]["restitution"])
        shapes = []
        bodies = []
        if obj["type"] == "box":
            low, high = obj["bounds_world"]["min"], obj["bounds_world"]["max"]
            shape = UsdGeom.Cube.Define(stage, root + "/Shape")
            shape.CreateSizeAttr(1)
            shape.AddTranslateOp().Set(
                Gf.Vec3d(*[(a + b) / 2 for a, b in zip(low, high, strict=True)])
            )
            shapes.append(shape.GetPrim())
            if obj["physics"]["motion_type"] != "static":
                api = UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
                api.CreateKinematicEnabledAttr(
                    obj["physics"]["motion_type"] == "kinematic"
                )
                UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(
                    obj["physics"]["mass_kg"]
                )
                bodies.append(root)
        else:
            for role, factor in (("Base", 1.3), ("Cap", 1.0)):
                part = UsdGeom.Xform.Define(stage, root + "/" + role)
                position = list(obj["position_world"])
                if role == "Base":
                    position[1] -= obj["travel"] + 1.5 * obj["cap_height"]
                part.AddTranslateOp().Set(Gf.Vec3d(*position))
                UsdPhysics.RigidBodyAPI.Apply(
                    part.GetPrim()
                ).CreateKinematicEnabledAttr(role == "Base")
                if role == "Cap":
                    UsdPhysics.MassAPI.Apply(part.GetPrim()).CreateMassAttr(
                        obj["physics"]["mass_kg"]
                    )
                shape = UsdGeom.Cylinder.Define(stage, root + "/" + role + "/Shape")
                shape.CreateAxisAttr("Y")
                shape.CreateRadiusAttr(obj["radius"] * factor)
                shape.CreateHeightAttr(obj["cap_height"])
                shapes.append(shape.GetPrim())
                bodies.append(root + "/" + role)
            joint = UsdPhysics.PrismaticJoint.Define(stage, root + "/Joint")
            joint.CreateAxisAttr("Y")
            joint.CreateLowerLimitAttr(-obj["travel"])
            joint.CreateUpperLimitAttr(0)
            joint.CreateBody0Rel().SetTargets([root + "/Base"])
            joint.CreateBody1Rel().SetTargets([root + "/Cap"])
            joint.CreateCollisionEnabledAttr(False)
            drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
            drive.CreateTypeAttr("force")
            drive.CreateTargetPositionAttr(0)
            drive.CreateStiffnessAttr(obj["stiffness"])
            drive.CreateDampingAttr(obj["damping"])
            drive.CreateMaxForceAttr(obj["max_force"])
        for prim in shapes:
            UsdPhysics.CollisionAPI.Apply(prim)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                material, materialPurpose="physics"
            )
        obj["physics_binding"] = {
            "material_prim": material_path,
            "collision_prims": [str(prim.GetPath()) for prim in shapes],
            "rigid_body_prims": bodies,
        }
    stage.GetRootLayer().Save()
    write_json(output / "asset_catalog.json", catalog)
    return output, spec_path


def _inspect(package):
    directory, source = package
    return inspect_physical_assets(directory, scene_spec=source)


def _failures(report):
    return [check["id"] for check in report["checks"] if not check["passed"]]


def test_five_categories_readback_does_not_claim_reality_or_semantic_accuracy(package):
    report = _inspect(package)
    assert report["passed"], _failures(report)
    assert report["coverage"]["categories"] == {
        name: 1 for name in ("floor", "cabinet", "table", "chair", "button")
    }
    assert report["material_assignment"]["unique_material_count"] == 5
    assert report["material_assignment"]["collision_prim_count"] == 6
    assert report["real_world_calibration"]["status"] == "NOT_EVALUATED"
    assert report["physical_response"]["status"] == "NOT_EVALUATED"
    assert report["semantic_accuracy"]["status"] == "NOT_EVALUATED"
    assert report["release_authorized"] is False
    assert all(len(value["sha256"]) == 64 for value in report["inputs"].values())


def test_catalog_cannot_certify_itself_without_original_spec(package):
    report = inspect_physical_assets(package[0])
    assert report["status"] == "NOT_EVALUATED"
    assert report["passed"] is False


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong_material",
        "friction",
        "remove_collision",
        "disable_collision",
        "mass",
        "kinematic",
        "disable_body",
        "shape",
        "joint",
        "extra_asset",
        "metadata",
        "extra_body",
        "button_position",
        "nonfinite_friction",
    ],
)
def test_usd_changes_fail_independent_readback(package, tamper):
    directory, _ = package
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    chair = stage.GetPrimAtPath("/World/Objects/chair")
    shape = stage.GetPrimAtPath("/World/Objects/chair/Shape")
    if tamper == "wrong_material":
        material = UsdShade.Material.Get(
            stage, "/World/Objects/floor/Looks/PhysicsMaterial"
        )
        UsdShade.MaterialBindingAPI(shape).Bind(material, materialPurpose="physics")
    elif tamper == "friction":
        prim = stage.GetPrimAtPath("/World/Objects/chair/Looks/PhysicsMaterial")
        UsdPhysics.MaterialAPI(prim).GetStaticFrictionAttr().Set(0.01)
    elif tamper == "remove_collision":
        shape.RemoveAPI(UsdPhysics.CollisionAPI)
    elif tamper == "disable_collision":
        UsdPhysics.CollisionAPI(shape).CreateCollisionEnabledAttr(False)
    elif tamper == "mass":
        UsdPhysics.MassAPI(chair).GetMassAttr().Set(99)
    elif tamper == "kinematic":
        UsdPhysics.RigidBodyAPI(chair).GetKinematicEnabledAttr().Set(True)
    elif tamper == "disable_body":
        UsdPhysics.RigidBodyAPI(chair).CreateRigidBodyEnabledAttr(False)
    elif tamper == "shape":
        UsdGeom.Cube(shape).GetSizeAttr().Set(2)
    elif tamper == "joint":
        joint = stage.GetPrimAtPath("/World/Objects/button/Joint")
        UsdPhysics.DriveAPI(joint, "linear").GetStiffnessAttr().Set(999)
    elif tamper == "extra_asset":
        UsdGeom.Xform.Define(stage, "/World/Objects/invented")
    elif tamper == "metadata":
        chair.SetCustomDataByKey("simready:physics_json", "{}")
    elif tamper == "extra_body":
        UsdPhysics.RigidBodyAPI.Apply(shape)
    elif tamper == "button_position":
        cap = stage.GetPrimAtPath("/World/Objects/button/Cap")
        cap.GetAttribute("xformOp:translate").Set(Gf.Vec3d(99, 1, 1))
    elif tamper == "nonfinite_friction":
        prim = stage.GetPrimAtPath("/World/Objects/chair/Looks/PhysicsMaterial")
        UsdPhysics.MaterialAPI(prim).GetStaticFrictionAttr().Set(float("nan"))
    stage.GetRootLayer().Save()
    report = _inspect(package)
    assert report["status"] == "FAIL", tamper
    assert _failures(report)
    json.dumps(report, allow_nan=False)


def test_matching_catalog_and_usd_tamper_cannot_replace_source_expectation(package):
    directory, _ = package
    catalog = read_json(directory / "asset_catalog.json")
    chair = next(obj for obj in catalog["objects"] if obj["id"] == "chair")
    chair["physics"]["mass_kg"] = 99
    write_json(directory / "asset_catalog.json", catalog)
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/chair")
    UsdPhysics.MassAPI(prim).GetMassAttr().Set(99)
    prim.SetCustomDataByKey("simready:physics_json", json.dumps(chair["physics"]))
    stage.GetRootLayer().Save()
    report = _inspect(package)
    assert "chair.catalog.mass_kg" in _failures(report)
    assert "chair.body.mass_kg" in _failures(report)


def test_claiming_measured_provenance_without_source_change_is_rejected(package):
    directory, _ = package
    catalog = read_json(directory / "asset_catalog.json")
    chair = next(obj for obj in catalog["objects"] if obj["id"] == "chair")
    chair["physics"]["provenance"]["mass_kg"]["source_kind"] = "measured"
    write_json(directory / "asset_catalog.json", catalog)
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/chair")
    prim.SetCustomDataByKey("simready:physics_json", json.dumps(chair["physics"]))
    stage.GetRootLayer().Save()
    report = _inspect(package)
    assert "chair.provenance.mass_kg.source_kind" in _failures(report)


def test_duplicate_asset_id_and_invalid_json_are_rejected(package):
    directory, _ = package
    catalog = read_json(directory / "asset_catalog.json")
    catalog["objects"].append(catalog["objects"][0])
    write_json(directory / "asset_catalog.json", catalog)
    with pytest.raises(ValueError, match="duplicate"):
        _inspect(package)


def test_cli_reopens_files_in_a_fresh_process_and_records_failure(package, tmp_path):
    directory, source = package
    report = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "simready.evaluation.physical_assets",
        "--scene-directory",
        str(directory),
        "--scene-spec",
        str(source),
        "--out",
        str(report),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert read_json(report)["passed"]
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    stage.RemovePrim("/World/Objects/button/Cap")
    stage.GetRootLayer().Save()
    report.unlink()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert read_json(report)["status"] == "FAIL"


def test_existing_result_is_never_overwritten(package, tmp_path):
    directory, source = package
    output = tmp_path / "existing.json"
    output.write_text("keep")
    from simready.evaluation.physical_assets import main

    with pytest.raises(FileExistsError):
        main(
            [
                "--scene-directory",
                str(directory),
                "--scene-spec",
                str(source),
                "--out",
                str(output),
            ]
        )
    assert output.read_text() == "keep"


def test_matching_invalid_negative_mass_is_not_a_valid_physical_contract(package):
    directory, source = package
    spec = read_json(source)
    next(obj for obj in spec["objects"] if obj["id"] == "chair")["physics"][
        "mass_kg"
    ] = -8
    write_json(source, spec)
    catalog = read_json(directory / "asset_catalog.json")
    chair = next(obj for obj in catalog["objects"] if obj["id"] == "chair")
    chair["physics"]["mass_kg"] = -8
    write_json(directory / "asset_catalog.json", catalog)
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/chair")
    UsdPhysics.MassAPI(prim).GetMassAttr().Set(-8)
    prim.SetCustomDataByKey("simready:physics_json", json.dumps(chair["physics"]))
    stage.GetRootLayer().Save()
    assert "chair.input_contract.positive_mass" in _failures(_inspect(package))


def test_hidden_collision_proxy_is_accepted_without_visual_coverage_claim(package):
    directory, source = package
    spec = read_json(source)
    next(obj for obj in spec["objects"] if obj["id"] == "cabinet")["visible"] = False
    write_json(source, spec)
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/cabinet")
    UsdGeom.Imageable(prim).CreateVisibilityAttr("invisible")
    stage.GetRootLayer().Save()
    report = _inspect(package)
    assert report["passed"], _failures(report)
    assert report["semantic_accuracy"]["status"] == "NOT_EVALUATED"


def test_undeclared_scale_cannot_validate_explicit_dynamic_properties(package):
    _, source = package
    spec = read_json(source)
    del spec["scale_provenance"]
    write_json(source, spec)
    report = _inspect(package)
    assert "chair.input_contract.metric_scale_declaration" in _failures(report)


def test_tiny_positive_mass_cannot_match_usd_auto_mass_zero(package):
    directory, source = package
    spec = read_json(source)
    next(obj for obj in spec["objects"] if obj["id"] == "chair")["physics"][
        "mass_kg"
    ] = 1e-9
    write_json(source, spec)
    catalog = read_json(directory / "asset_catalog.json")
    chair = next(obj for obj in catalog["objects"] if obj["id"] == "chair")
    chair["physics"]["mass_kg"] = 1e-9
    write_json(directory / "asset_catalog.json", catalog)
    stage = Usd.Stage.Open(str(directory / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/chair")
    prim.SetCustomDataByKey("simready:physics_json", json.dumps(chair["physics"]))
    mass = UsdPhysics.MassAPI(prim).GetMassAttr()
    mass.Set(1e-9)
    stage.GetRootLayer().Save()
    assert _inspect(package)["passed"]
    mass.Set(0)
    stage.GetRootLayer().Save()
    assert "chair.body.mass_kg" in _failures(_inspect(package))
