"""Read composed USD independently and compare physical assets to input evidence.

This CPU inspection verifies assignment and authored values, not material
prediction accuracy or simulated/real dynamics. It intentionally does not import
the physical authoring implementation. Run the module in a fresh process for an
independent worker, supplying the original scene specification as the expectation.
"""

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, loads_json, read_json, write_json

_CONTACT_DEFAULTS = {
    "static_friction": 0.8,
    "dynamic_friction": 0.7,
    "restitution": 0.0,
}
_BUTTON_DEFAULTS = {
    "radius": 0.02,
    "cap_height": 0.01,
    "travel": 0.004,
    "stiffness": 400.0,
    "damping": 4.0,
    "max_force": 8.0,
    "mass": 0.02,
}
_UNITS = {
    "mass_kg": "kg",
    "center_of_mass_m": "m",
    "inertia_kg_m2": "kg*m^2",
    "contact": "1",
}


def _matches(actual: Any, expected: Any, *, absolute_tolerance: float = 1e-7) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(
                _matches(actual[key], value, absolute_tolerance=absolute_tolerance)
                for key, value in expected.items()
            )
        )
    if type(expected) is bool:
        return type(actual) is bool and actual is expected
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and len(actual) == len(expected)
            and all(
                _matches(a, b, absolute_tolerance=absolute_tolerance)
                for a, b in zip(actual, expected, strict=True)
            )
        )
    if type(expected) in (int, float):
        return (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=2e-6, abs_tol=absolute_tolerance)
        )
    return actual == expected


def _json_detail(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_detail(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_detail(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "NONFINITE:" + str(value)
    return value


def _record(
    checks: list[dict[str, Any]], name: str, passed: bool, **details: Any
) -> None:
    checks.append(
        {
            "id": name,
            "status": "PASS" if passed else "FAIL",
            "passed": bool(passed),
            "details": _json_detail(details),
        }
    )


def _value(checks: list[dict[str, Any]], name: str, actual: Any, expected: Any) -> None:
    _record(checks, name, _matches(actual, expected), actual=actual, expected=expected)


def _objects(document: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    from pxr import Sdf

    values = document.get("objects")
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError(f"{name}.objects must be a list of at most 256 objects")
    indexed = {}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid object in {name}")
        key = item.get("id")
        if (
            not isinstance(key, str)
            or not Sdf.Path.IsValidIdentifier(key)
            or key in indexed
        ):
            raise ValueError(f"{name} contains an invalid or duplicate object id")
        if item.get("type") not in {"box", "button"}:
            raise ValueError(f"Unsupported {name} object geometry")
        indexed[key] = item
    return indexed


def _expected_physics(obj: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Evaluate only the documented input defaults, independently of authoring."""
    given = obj.get("physics", {})
    if not isinstance(given, dict):
        raise ValueError("Object physics must be an object")
    contact = dict(_CONTACT_DEFAULTS)
    collision = spec.get("collision", {})
    contact.update({key: collision[key] for key in contact if key in collision})
    contact.update(given.get("contact", {}))
    expected = {
        "motion_type": given.get(
            "motion_type", "dynamic" if obj["type"] == "button" else "static"
        ),
        "contact": contact,
    }
    if obj["type"] == "button":
        expected["mass_kg"] = given.get("mass_kg", obj.get("mass", 0.02))
    for key in ("mass_kg", "center_of_mass_m", "inertia_kg_m2"):
        if key in given:
            expected[key] = given[key]
    return expected


def _paths(obj: dict[str, Any]) -> dict[str, Any]:
    root = "/World/Objects/" + obj["id"]
    if obj["type"] == "box":
        collisions = [root + "/Shape"]
        bodies = (
            []
            if obj.get("physics", {}).get("motion_type", "static") == "static"
            else [root]
        )
    else:
        collisions = [root + "/Base/Shape", root + "/Cap/Shape"]
        bodies = [root + "/Base", root + "/Cap"]
    return {
        "root": root,
        "material_prim": root + "/Looks/PhysicsMaterial",
        "collision_prims": collisions,
        "rigid_body_prims": bodies,
    }


def _physical_contract(
    checks: list[dict[str, Any]],
    obj: dict[str, Any],
    expected: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    """Reject impossible declarations even if a tampered USD matches them."""
    name = obj["id"] + ".input_contract."
    motion = expected["motion_type"]
    _record(
        checks,
        name + "motion",
        motion in {"static", "dynamic", "kinematic"}
        and (obj["type"] != "button" or motion == "dynamic"),
    )
    contact = expected["contact"]
    for key, upper in (
        ("static_friction", 10),
        ("dynamic_friction", 10),
        ("restitution", 1),
    ):
        value = contact.get(key)
        _record(
            checks,
            name + key,
            type(value) in (int, float)
            and math.isfinite(value)
            and 0 <= value <= upper,
            value=value,
        )
    _record(
        checks,
        name + "friction_order",
        contact["dynamic_friction"] <= contact["static_friction"],
    )
    if motion == "dynamic" or "mass_kg" in expected:
        mass = expected.get("mass_kg")
        _record(
            checks,
            name + "positive_mass",
            type(mass) in (int, float) and math.isfinite(mass) and 1e-9 <= mass <= 1e12,
            value=mass,
        )
    for key, low, high in (
        ("center_of_mass_m", -1e6, 1e6),
        ("inertia_kg_m2", 1e-12, 1e18),
    ):
        if key not in expected:
            continue
        value = expected[key]
        valid = (
            isinstance(value, list)
            and len(value) == 3
            and all(
                type(x) in (int, float) and math.isfinite(x) and low <= x <= high
                for x in value
            )
        )
        if valid and key == "inertia_kg_m2":
            valid = 2 * max(value) <= sum(value) * (1 + 1e-12)
        _record(checks, name + key, valid and "mass_kg" in expected, value=value)
    supplied = obj.get("physics", {})
    needs_scale = (obj["type"] == "box" and motion != "static") or (
        obj["type"] == "button"
        and bool(
            set(supplied)
            & {"motion_type", "mass_kg", "center_of_mass_m", "inertia_kg_m2"}
        )
    )
    provenance = spec.get("scale_provenance", {})
    declared = (
        "source_to_world" in spec
        and provenance.get("status") == "verified"
        and bool(provenance.get("method"))
        and bool(provenance.get("evidence"))
    )
    _record(
        checks,
        name + "metric_scale_declaration",
        not needs_scale or bool(declared),
        required=needs_scale,
        independently_verified=False,
    )


def _body(
    stage: Any,
    checks: list[dict[str, Any]],
    name: str,
    path: str,
    motion: str,
    expected: dict[str, Any],
) -> None:
    from pxr import Usd, UsdGeom, UsdPhysics

    prim = stage.GetPrimAtPath(path)
    valid = bool(prim)
    _record(checks, name + ".exists", valid, prim=path)
    if not valid:
        return
    has_rigid = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    _value(checks, name + ".rigid_body", has_rigid, motion != "static")
    if has_rigid:
        body = UsdPhysics.RigidBodyAPI(prim)
        _value(checks, name + ".enabled", body.GetRigidBodyEnabledAttr().Get(), True)
        _value(
            checks,
            name + ".kinematic",
            body.GetKinematicEnabledAttr().Get(),
            motion == "kinematic",
        )
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        _value(
            checks,
            name + ".unscaled_axes",
            [[float(matrix[row][column]) for column in range(3)] for row in range(3)],
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        )
    for field, getter in (
        ("mass_kg", "GetMassAttr"),
        ("center_of_mass_m", "GetCenterOfMassAttr"),
        ("inertia_kg_m2", "GetDiagonalInertiaAttr"),
    ):
        if field not in expected:
            continue
        api = UsdPhysics.MassAPI(prim)
        value = (
            getattr(api, getter)().Get() if prim.HasAPI(UsdPhysics.MassAPI) else None
        )
        if value is not None and field != "mass_kg":
            value = list(value)
        _record(
            checks,
            name + "." + field,
            _matches(value, expected[field], absolute_tolerance=0),
            actual=value,
            expected=expected[field],
        )
    if "inertia_kg_m2" in expected and prim.HasAPI(UsdPhysics.MassAPI):
        quaternion = UsdPhysics.MassAPI(prim).GetPrincipalAxesAttr().Get()
        actual = (
            None
            if quaternion is None
            else [quaternion.GetReal(), *quaternion.GetImaginary()]
        )
        # The contract's principal inertia vector is expressed in object axes.
        _record(
            checks,
            name + ".principal_axes",
            _matches(actual, [1, 0, 0, 0]) or _matches(actual, [-1, 0, 0, 0]),
            actual=actual,
        )


def _provenance(
    stage: Any,
    checks: list[dict[str, Any]],
    obj: dict[str, Any],
    catalog_obj: dict[str, Any],
    root: str,
    spec: dict[str, Any],
) -> None:
    name = obj["id"]
    physics = catalog_obj.get("physics", {})
    prim = stage.GetPrimAtPath(root)
    encoded = prim.GetCustomDataByKey("simready:physics_json") if prim else None
    try:
        recorded = loads_json(encoded) if isinstance(encoded, str) else None
    except (ValueError, TypeError):
        recorded = None
    _value(checks, name + ".usd_provenance_manifest", recorded, physics)
    validation = physics.get("validation", {})
    _value(
        checks,
        name + ".real_world_validated",
        validation.get("real_world_validated"),
        False,
    )
    _value(
        checks,
        name + ".independently_verified",
        validation.get("independently_verified"),
        False,
    )
    _value(checks, name + ".validation_status", validation.get("status"), "unvalidated")
    provided = obj.get("physics", {}).get("provenance", {})
    actual = physics.get("provenance", {})
    numeric = {}
    for field in ("mass_kg", "center_of_mass_m", "inertia_kg_m2", "contact"):
        if field not in physics:
            continue
        value = physics[field]
        if isinstance(value, dict):
            numeric.update({field + "." + k: v for k, v in value.items()})
        elif isinstance(value, list):
            numeric.update(
                {
                    field + "." + axis: item
                    for axis, item in zip("xyz", value, strict=True)
                }
            )
        else:
            numeric[field] = value
    _value(checks, name + ".provenance_coverage", sorted(actual), sorted(numeric))
    for key, expected in provided.items():
        got = actual.get(key, {})
        for field, value in expected.items():
            _value(
                checks, name + ".provenance." + key + "." + field, got.get(field), value
            )
    for key, evidence in actual.items():
        valid = (
            isinstance(evidence, dict)
            and bool(evidence.get("source"))
            and evidence.get("source_kind")
            in {
                "default",
                "user_declared",
                "category_prior",
                "catalog",
                "measured",
                "derived",
                "vision_prior",
            }
        )
        unit = _UNITS.get(key.split(".")[0])
        valid = valid and unit is not None and evidence.get("unit") == unit
        interval = evidence.get("interval") if isinstance(evidence, dict) else None
        if interval is not None:
            valid = valid and isinstance(interval, list) and len(interval) == 2
            valid = valid and all(
                type(x) in (int, float) and math.isfinite(x) for x in interval
            )
            valid = (
                valid and key in numeric and interval[0] <= numeric[key] <= interval[1]
            )
        _record(checks, name + ".provenance_record." + key, valid, record=evidence)
        if key not in provided and isinstance(evidence, dict):
            base, _, component = key.partition(".")
            explicit = base in obj.get("physics", {})
            if base == "contact":
                explicit = component in obj.get("physics", {}).get(
                    "contact", {}
                ) or component in spec.get("collision", {})
            if key == "mass_kg" and obj["type"] == "button":
                explicit = explicit or "mass" in obj
            _value(
                checks,
                name + ".provenance_origin." + key,
                evidence.get("source_kind"),
                "user_declared" if explicit else "default",
            )
            _value(checks, name + ".provenance_interval." + key, interval, None)


def _geometry(
    stage: Any, checks: list[dict[str, Any]], obj: dict[str, Any], root: str
) -> None:
    from pxr import Usd, UsdGeom, UsdPhysics

    name = obj["id"]
    if obj["type"] == "box":
        prim = stage.GetPrimAtPath(root + "/Shape")
        _record(checks, name + ".box_shape", bool(prim) and prim.IsA(UsdGeom.Cube))
        if prim:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
                useExtentsHint=False,
            )
            bounds = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            _value(
                checks,
                name + ".bounds.min",
                list(bounds.GetMin()),
                obj["bounds_world"]["min"],
            )
            _value(
                checks,
                name + ".bounds.max",
                list(bounds.GetMax()),
                obj["bounds_world"]["max"],
            )
        return
    expected = {**_BUTTON_DEFAULTS, **obj}
    axis = expected.get("axis", "Z")
    for role, factor in (("Base", 1.3), ("Cap", 1.0)):
        prim = stage.GetPrimAtPath(root + "/" + role + "/Shape")
        valid = bool(prim) and prim.IsA(UsdGeom.Cylinder)
        _record(checks, name + "." + role + ".cylinder", valid)
        if valid:
            cylinder = UsdGeom.Cylinder(prim)
            _value(
                checks,
                name + "." + role + ".radius",
                cylinder.GetRadiusAttr().Get(),
                expected["radius"] * factor,
            )
            _value(
                checks,
                name + "." + role + ".height",
                cylinder.GetHeightAttr().Get(),
                expected["cap_height"],
            )
            _value(
                checks, name + "." + role + ".axis", cylinder.GetAxisAttr().Get(), axis
            )
            position = list(expected["position_world"])
            if role == "Base":
                position["XYZ".index(axis)] -= (
                    expected["travel"] + expected["cap_height"] * 1.5
                )
            transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()
            )
            _value(
                checks,
                name + "." + role + ".position_world",
                list(transform.ExtractTranslation()),
                position,
            )
    joint = UsdPhysics.PrismaticJoint.Get(stage, root + "/Joint")
    _record(checks, name + ".joint_exists", bool(joint))
    if joint:
        for label, actual, target in (
            ("axis", joint.GetAxisAttr().Get(), axis),
            ("lower_limit", joint.GetLowerLimitAttr().Get(), -expected["travel"]),
            ("upper_limit", joint.GetUpperLimitAttr().Get(), 0.0),
            ("enabled", joint.GetJointEnabledAttr().Get(), True),
            ("collision_enabled", joint.GetCollisionEnabledAttr().Get(), False),
            (
                "body0",
                [str(x) for x in joint.GetBody0Rel().GetTargets()],
                [root + "/Base"],
            ),
            (
                "body1",
                [str(x) for x in joint.GetBody1Rel().GetTargets()],
                [root + "/Cap"],
            ),
        ):
            _value(checks, name + ".joint." + label, actual, target)
        drive = UsdPhysics.DriveAPI(joint.GetPrim(), "linear")
        for label, actual, target in (
            ("type", drive.GetTypeAttr().Get(), "force"),
            ("target", drive.GetTargetPositionAttr().Get(), 0.0),
            ("stiffness", drive.GetStiffnessAttr().Get(), expected["stiffness"]),
            ("damping", drive.GetDampingAttr().Get(), expected["damping"]),
            ("max_force", drive.GetMaxForceAttr().Get(), expected["max_force"]),
        ):
            _value(checks, name + ".drive." + label, actual, target)


def inspect_physical_assets(
    directory: str | Path, *, scene_spec: str | Path | None = None
) -> dict[str, Any]:
    """Compare each authored object's USD values against a separate input spec.

    Missing reference specification returns NOT_EVALUATED. Malformed manifests,
    missing files and unavailable USD bindings raise; value/assignment mismatches
    produce a FAIL report. No dynamics or real-world accuracy is inferred.
    """
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    root = Path(directory).resolve()
    catalog_path = root / "asset_catalog.json"
    scene_path = root / "scene.usda"
    catalog = read_json(catalog_path)
    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "physical_asset_inspection",
        "scope": "independent composed USD assignment and value readback",
        "passed": False,
        "status": "NOT_EVALUATED",
        "checks": [],
        "assets": [],
        "inputs": {
            "scene": {"path": str(scene_path), "sha256": digest(scene_path)},
            "catalog": {"path": str(catalog_path), "sha256": digest(catalog_path)},
        },
        "semantic_accuracy": {
            "status": "NOT_EVALUATED",
            "reason": "No independent semantic ground truth is supplied.",
        },
        "physical_response": {
            "status": "NOT_EVALUATED",
            "reason": "No dynamics rollout or convergence probe is run by this inspector.",
        },
        "real_world_calibration": {
            "status": "NOT_EVALUATED",
            "reason": "USD assignment is not evidence of real-world parameter accuracy.",
        },
        "release_authorized": False,
    }
    if scene_spec is None:
        report["reason"] = (
            "Original scene specification is required; candidate catalog cannot validate itself."
        )
        return report
    spec_path = Path(scene_spec).resolve()
    spec = read_json(spec_path)
    report["inputs"]["scene_spec"] = {
        "path": str(spec_path),
        "sha256": digest(spec_path),
    }
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise ValueError("Expected scene specification schema version 1")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Expected asset catalog schema version 1")
    objects = _objects(spec, "scene_spec")
    catalog_objects = _objects(catalog, "asset_catalog")
    checks = report["checks"]
    _record(checks, "nonempty_assets", bool(objects), count=len(objects))
    _value(checks, "asset_ids", sorted(catalog_objects), sorted(objects))
    stage = Usd.Stage.Open(str(scene_path))
    if stage is None:
        raise ValueError("Cannot open composed scene USD")
    _record(
        checks,
        "composition",
        not stage.GetCompositionErrors(),
        errors=[str(x) for x in stage.GetCompositionErrors()],
    )
    _value(checks, "meters_per_unit", UsdGeom.GetStageMetersPerUnit(stage), 1.0)
    _value(checks, "up_axis", UsdGeom.GetStageUpAxis(stage), "Z")
    dependencies = []
    for layer in stage.GetUsedLayers():
        if layer.anonymous:
            continue
        path = Path(layer.realPath).resolve()
        local = path.is_relative_to(root) and path.is_file()
        _record(checks, "dependency." + layer.identifier, local, path=str(path))
        if local:
            dependencies.append(
                {"path": str(path.relative_to(root)), "sha256": digest(path)}
            )
    report["dependencies"] = sorted(dependencies, key=lambda x: x["path"])
    container = stage.GetPrimAtPath("/World/Objects")
    actual_ids = (
        sorted(
            child.GetName()
            for child in container.GetChildren()
            if not child.IsA(UsdShade.Material)
        )
        if container
        else []
    )
    _value(checks, "usd_asset_ids", actual_ids, sorted(objects))
    assignments: dict[str, Any] = {}
    material_paths = set()
    for key, obj in objects.items():
        start = len(checks)
        catalog_obj = catalog_objects.get(key, {})
        expected = _expected_physics(obj, spec)
        _physical_contract(checks, obj, expected, spec)
        paths = _paths(obj)
        usd_root = paths["root"]
        _value(
            checks,
            key + ".category",
            catalog_obj.get("category"),
            obj.get("category", obj["type"]),
        )
        _value(checks, key + ".geometry_type", catalog_obj.get("type"), obj["type"])
        physics = catalog_obj.get("physics", {})
        for field, target in expected.items():
            _value(checks, key + ".catalog." + field, physics.get(field), target)
        binding = catalog_obj.get("physics_binding", {})
        for field in ("material_prim", "collision_prims", "rigid_body_prims"):
            actual, target = binding.get(field), paths[field]
            if isinstance(actual, list):
                actual, target = sorted(actual), sorted(target)
            _value(checks, key + ".binding." + field, actual, target)
        _provenance(stage, checks, obj, catalog_obj, usd_root, spec)
        root_prim = stage.GetPrimAtPath(usd_root)
        for metadata, expected_metadata in (
            ("object_id", key),
            ("semantic_category", obj.get("category", obj["type"])),
            ("geometry_type", obj["type"]),
        ):
            actual_metadata = (
                root_prim.GetCustomDataByKey("simready:" + metadata)
                if root_prim
                else None
            )
            _value(
                checks,
                key + ".metadata." + metadata,
                actual_metadata,
                expected_metadata,
            )
        if root_prim:
            _value(
                checks,
                key + ".visibility",
                UsdGeom.Imageable(root_prim).ComputeVisibility(),
                "inherited" if obj.get("visible", True) else "invisible",
            )
        observed_colliders = (
            sorted(
                str(p.GetPath())
                for p in Usd.PrimRange(root_prim)
                if p.HasAPI(UsdPhysics.CollisionAPI)
            )
            if root_prim
            else []
        )
        _value(
            checks,
            key + ".collision_inventory",
            observed_colliders,
            sorted(paths["collision_prims"]),
        )
        mapping = []
        for path in paths["collision_prims"]:
            prim = stage.GetPrimAtPath(path)
            valid = bool(prim) and prim.HasAPI(UsdPhysics.CollisionAPI)
            _record(checks, key + ".collision." + path, valid, prim=path)
            if not valid:
                continue
            _value(
                checks,
                key + ".collision_enabled." + path,
                UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get(),
                True,
            )
            material, relationship = UsdShade.MaterialBindingAPI(
                prim
            ).ComputeBoundMaterial(materialPurpose="physics")
            material_path = str(material.GetPath()) if material else None
            _value(
                checks,
                key + ".material_binding." + path,
                material_path,
                paths["material_prim"],
            )
            actual_contact = {}
            valid_material = bool(material) and material.GetPrim().HasAPI(
                UsdPhysics.MaterialAPI
            )
            _record(checks, key + ".physics_material." + path, valid_material)
            if valid_material:
                api = UsdPhysics.MaterialAPI(material.GetPrim())
                for field, getter in (
                    ("static_friction", "GetStaticFrictionAttr"),
                    ("dynamic_friction", "GetDynamicFrictionAttr"),
                    ("restitution", "GetRestitutionAttr"),
                ):
                    actual_contact[field] = getattr(api, getter)().Get()
                    _value(
                        checks,
                        key + ".material." + path + "." + field,
                        actual_contact[field],
                        expected["contact"][field],
                    )
                material_paths.add(material_path)
            mapping.append(
                {
                    "collision_prim": path,
                    "material_prim": material_path,
                    "binding_relationship": str(relationship.GetPath())
                    if relationship
                    else None,
                    "contact": _json_detail(actual_contact),
                }
            )
        assignments[key] = mapping
        if obj["type"] == "button":
            _body(stage, checks, key + ".base", usd_root + "/Base", "kinematic", {})
            _body(stage, checks, key + ".cap", usd_root + "/Cap", "dynamic", expected)
        else:
            _body(
                stage,
                checks,
                key + ".body",
                usd_root,
                expected["motion_type"],
                expected,
            )
        # Additional or incorrectly nested rigid bodies alter PhysX behavior.
        observed_bodies = (
            sorted(
                str(p.GetPath())
                for p in Usd.PrimRange(root_prim)
                if p.HasAPI(UsdPhysics.RigidBodyAPI)
            )
            if root_prim
            else []
        )
        _value(
            checks,
            key + ".body_inventory",
            observed_bodies,
            sorted(paths["rigid_body_prims"]),
        )
        _geometry(stage, checks, obj, usd_root)
        passed = all(check["passed"] for check in checks[start:])
        report["assets"].append(
            {
                "id": key,
                "category": obj.get("category", obj["type"]),
                "geometry_type": obj["type"],
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "expected_physics": expected,
                "failed_checks": [
                    check["id"] for check in checks[start:] if not check["passed"]
                ],
            }
        )
    report["material_assignment"] = {
        "asset_count": len(objects),
        "unique_material_count": len(material_paths),
        "collision_prim_count": sum(len(items) for items in assignments.values()),
        "by_asset": assignments,
    }
    report["coverage"] = {
        "expected_assets": len(objects),
        "catalog_assets": len(catalog_objects),
        "usd_assets": len(actual_ids),
        "passed_assets": sum(asset["passed"] for asset in report["assets"]),
        "categories": dict(
            sorted(
                Counter(
                    obj.get("category", obj["type"]) for obj in objects.values()
                ).items()
            )
        ),
        "scope": "declared object inventory; completeness against the original scene is not evaluated",
    }
    report["passed"] = all(check["passed"] for check in checks)
    report["status"] = "PASS" if report["passed"] else "FAIL"
    report["structural_validation"] = report["status"]
    report["authored_value_validation"] = report["status"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-directory", type=Path, required=True)
    parser.add_argument("--scene-spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    report = inspect_physical_assets(args.scene_directory, scene_spec=args.scene_spec)
    write_json(args.out, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
