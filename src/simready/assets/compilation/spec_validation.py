"""Validate object and numeric declarations without writing USD."""

import numpy as np

from simready.assets.physics.physical_spec import normalize_physics
from simready.geometry.transforms_validation import finite_array


def _number(value, name, lower, upper):
    if (
        type(value) not in (int, float)
        or not np.isfinite(value)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be within {lower}..{upper}")
    return float(value)


def _integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer within {lower}..{upper}")
    return value


def _keys(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"Unknown or malformed {name} fields")


def _bounds(value):
    _keys(value, {"min", "max"}, "bounds_world")
    low = finite_array(value.get("min"), "bounds min", (3,))
    high = finite_array(value.get("max"), "bounds max", (3,))
    if np.any(high <= low) or np.max(np.abs([low, high])) > 1e9:
        raise ValueError("Bounds must have positive extent and magnitude <= 1e9")
    return low, high


def _objects(spec, *, contact, declared_scale):
    from pxr import Sdf

    values = spec.get("objects", [])
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("objects must be a list with at most 256 entries")
    result, seen = [], set()
    common = {
        "id",
        "type",
        "category",
        "evidence",
        "color",
        "roughness",
        "physics",
        "visible",
    }
    for value in values:
        if not isinstance(value, dict) or value.get("type") not in {"box", "button"}:
            raise ValueError("Explicit object type must be box or button")
        kind = value["type"]
        allowed = (
            {"bounds_world"}
            if kind == "box"
            else {
                "position_world",
                "axis",
                "radius",
                "travel",
                "stiffness",
                "damping",
                "mass",
                "max_force",
                "cap_height",
            }
        )
        _keys(value, common | allowed, "object")
        name = value.get("id")
        if (
            not isinstance(name, str)
            or not Sdf.Path.IsValidIdentifier(name)
            or name in seen
        ):
            raise ValueError("Object id must be a unique USD identifier")
        seen.add(name)
        color = finite_array(
            value.get(
                "color", [0.55, 0.58, 0.62] if kind == "box" else [0.8, 0.1, 0.05]
            ),
            "color",
            (3,),
        )
        if np.any(color < 0) or np.any(color > 1):
            raise ValueError("color must be linear RGB within 0..1")
        evidence = value.get("evidence", "")
        category = value.get("category", kind)
        if (
            not isinstance(evidence, str)
            or not isinstance(category, str)
            or not category
        ):
            raise ValueError("Object evidence and category must be strings")
        if type(value.get("visible", True)) is not bool:
            raise ValueError("Object visible must be boolean")
        obj = dict(
            id=name,
            type=kind,
            category=category,
            visible=value.get("visible", True),
            evidence=evidence,
            color=color.tolist(),
            roughness=_number(value.get("roughness", 0.6), "roughness", 0, 1),
            source="explicit_scene_spec",
            estimate_status="user_declared_not_independently_verified",
            physical_parameters_measured=False,
            semantic_function_verified=False,
        )
        if kind == "box":
            low, high = _bounds(value.get("bounds_world"))
        else:
            position = finite_array(value.get("position_world"), "position_world", (3,))
            axis = value.get("axis", "Z")
            if axis not in {"X", "Y", "Z"} or np.max(np.abs(position)) > 1e9:
                raise ValueError("Button axis must be X/Y/Z and position bounded")
            obj.update(position_world=position.tolist(), axis=axis)
            for key, default, lower, upper in [
                ("radius", 0.02, 0.001, 2),
                ("travel", 0.004, 0.0001, 0.5),
                ("cap_height", 0.01, 0.001, 1),
                ("stiffness", 400.0, 0, 1e6),
                ("damping", 4.0, 0, 1e5),
                ("mass", 0.02, 0.0001, 100),
                ("max_force", 8.0, 0.001, 1e6),
            ]:
                obj[key] = _number(value.get(key, default), key, lower, upper)
            extent = np.full(3, obj["radius"] * 1.5)
            extent["XYZ".index(axis)] = obj["travel"] + obj["cap_height"] * 2
            low, high = position - extent, position + extent
            obj.update(
                signal_semantics="unbound physical spring; no automatic device-state transition",
                inward_axis_world=(-np.eye(3)["XYZ".index(axis)]).tolist(),
            )
        obj["bounds_world"] = {"min": low.tolist(), "max": high.tolist()}
        if "physics" in value and not isinstance(value["physics"], dict):
            raise ValueError("object.physics must be a mapping")
        obj["physics"] = normalize_physics(
            value.get("physics"),
            geometry_type=kind,
            fallback_contact=contact,
            metric_scale_declared=declared_scale,
            legacy_mass_kg=obj.get("mass"),
            legacy_mass_explicit="mass" in value,
            contact_explicit=set(spec.get("collision", {})),
        )
        if kind == "button":
            obj["mass"] = obj["physics"]["mass_kg"]
        result.append(obj)
    return result
