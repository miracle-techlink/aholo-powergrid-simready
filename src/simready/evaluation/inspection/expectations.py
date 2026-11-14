"""Interpret expected physics directly from input evidence, without the asset writer."""

import math
from typing import Any

from simready.evaluation.inspection.comparison import _record

_CONTACT_DEFAULTS = {
    "static_friction": 0.8,
    "dynamic_friction": 0.7,
    "restitution": 0.0,
}


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
