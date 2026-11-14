"""Inspect composed USD body, provenance and geometry against independent expectations."""

import math
from typing import Any

from simready.core.artifacts import loads_json
from simready.evaluation.inspection.comparison import _matches, _record, _value

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
