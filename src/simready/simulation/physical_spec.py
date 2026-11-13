"""Strict SI property declarations with provenance, not physical identification.

Scene schema v1 accepts an optional ``objects[*].physics`` declaration. Semantic
category and geometry type remain separate. Values are simulation hypotheses;
even a ``measured`` source label is a caller assertion, not independent evidence.
Inertia is a positive diagonal in the body's local axes, never a full tensor.
"""

from collections.abc import Mapping
from math import isfinite
from typing import Any

_CONTACT_DEFAULTS = {
    "static_friction": 0.8,
    "dynamic_friction": 0.7,
    "restitution": 0.0,
}
_SOURCE_KINDS = {
    "default",
    "user_declared",
    "category_prior",
    "catalog",
    "measured",
    "derived",
    "vision_prior",
}


def _mapping(value: object, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError(f"Unknown or malformed {name} fields")
    return value


def _scalar(value: object, name: str, lower: float, upper: float) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{name} must be within {lower}..{upper}")
    return result


def _vector(value: object, name: str, lower: float, upper: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain three local-axis values")
    return [_scalar(item, name, lower, upper) for item in value]


def normalize_physics(
    value: object,
    *,
    geometry_type: str,
    fallback_contact: Mapping[str, float],
    metric_scale_declared: bool,
    legacy_mass_kg: float | None = None,
    legacy_mass_explicit: bool = False,
    contact_explicit: set[str] | None = None,
) -> dict[str, Any]:
    """Normalize a declaration without promoting its source into validation.

    Boxes default to static. New moving boxes require declared metric scale;
    dynamic boxes additionally require explicit mass. A button's historic
    dynamic cap/kinematic base remains usable without a new physics block.
    Explicit button mass, COM, inertia or motion overrides require metric scale.
    Provenance for vector components uses ``center_of_mass_m.x`` (and y/z),
    likewise ``inertia_kg_m2.x``. Intervals always describe one scalar value.
    """
    if geometry_type not in {"box", "button"}:
        raise ValueError("physics geometry_type must be box or button")
    data = _mapping(
        value if value is not None else {},
        {
            "motion_type",
            "mass_kg",
            "center_of_mass_m",
            "inertia_kg_m2",
            "contact",
            "provenance",
        },
        "physics",
    )
    motion = data.get(
        "motion_type", "dynamic" if geometry_type == "button" else "static"
    )
    if not isinstance(motion, str) or motion not in {"static", "dynamic", "kinematic"}:
        raise ValueError("physics.motion_type must be static, dynamic or kinematic")
    if geometry_type == "button" and motion != "dynamic":
        raise ValueError("button physics.motion_type describes its dynamic cap")
    if not metric_scale_declared and (
        (geometry_type == "box" and motion != "static")
        or (
            geometry_type == "button"
            and set(data)
            & {
                "motion_type",
                "mass_kg",
                "center_of_mass_m",
                "inertia_kg_m2",
            }
        )
    ):
        raise ValueError("Moving physics overrides require declared metric scale")
    result: dict[str, Any] = {"motion_type": motion}
    # (value, unit, whether explicitly declared, allowed lower/upper bounds).
    parameters: dict[str, tuple[float, str, bool, float, float]] = {}
    if geometry_type == "button":
        mass = _scalar(data.get("mass_kg", legacy_mass_kg), "mass_kg", 1e-9, 1e12)
        if "mass_kg" in data and legacy_mass_explicit and mass != legacy_mass_kg:
            raise ValueError("button mass and physics.mass_kg conflict")
        result["mass_kg"] = mass
        parameters["mass_kg"] = (
            mass,
            "kg",
            "mass_kg" in data or legacy_mass_explicit,
            1e-9,
            1e12,
        )
    elif "mass_kg" in data:
        mass = _scalar(data["mass_kg"], "mass_kg", 1e-9, 1e12)
        result["mass_kg"] = mass
        parameters["mass_kg"] = (mass, "kg", True, 1e-9, 1e12)
    elif motion == "dynamic":
        raise ValueError("Dynamic box requires explicit physics.mass_kg")
    for key, unit, lower, upper in [
        ("center_of_mass_m", "m", -1e6, 1e6),
        ("inertia_kg_m2", "kg*m^2", 1e-12, 1e18),
    ]:
        if key not in data:
            continue
        if "mass_kg" not in result:
            raise ValueError(f"{key} requires explicit mass_kg")
        values = _vector(data[key], key, lower, upper)
        if key == "inertia_kg_m2" and 2 * max(values) > sum(values) * (1 + 1e-12):
            raise ValueError("Principal inertia must satisfy triangle inequalities")
        result[key] = values
        parameters.update(
            {
                f"{key}.{axis}": (item, unit, True, lower, upper)
                for axis, item in zip("xyz", values, strict=True)
            }
        )
    supplied_contact = _mapping(
        data.get("contact", {}), set(_CONTACT_DEFAULTS), "physics.contact"
    )
    contact = {}
    for key, default in _CONTACT_DEFAULTS.items():
        contact[key] = _scalar(
            supplied_contact.get(key, fallback_contact.get(key, default)),
            f"contact.{key}",
            0,
            1 if key == "restitution" else 10,
        )
        parameters[f"contact.{key}"] = (
            contact[key],
            "1",
            key in supplied_contact or key in (contact_explicit or set()),
            0,
            1 if key == "restitution" else 10,
        )
    if contact["dynamic_friction"] > contact["static_friction"]:
        raise ValueError("dynamic_friction must not exceed static_friction")
    result["contact"] = contact
    supplied_provenance = _mapping(
        data.get("provenance", {}), set(parameters), "physics.provenance"
    )
    provenance = {}
    for key, (number, unit, explicit, lower, upper) in parameters.items():
        record = _mapping(
            supplied_provenance.get(key, {}),
            {
                "source_kind",
                "source",
                "unit",
                "interval",
            },
            f"physics.provenance.{key}",
        )
        if key in supplied_provenance and not {"source_kind", "source", "unit"} <= set(
            record
        ):
            raise ValueError(
                f"Provenance for {key} requires source_kind, source and unit"
            )
        source_kind = record.get(
            "source_kind", "user_declared" if explicit else "default"
        )
        if not isinstance(source_kind, str) or source_kind not in _SOURCE_KINDS:
            raise ValueError(f"Unsupported source_kind for {key}")
        source = record.get(
            "source",
            "Explicit scene specification; not independently verified"
            if explicit
            else "Inherited simulation preset; not a measurement",
        )
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"Provenance source for {key} must be nonempty")
        if record.get("unit", unit) != unit:
            raise ValueError(f"{key} unit must be {unit}")
        interval = record.get("interval")
        if interval is not None:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise ValueError(f"{key} interval must be null or [low, high]")
            interval = [
                _scalar(item, f"{key} interval", lower, upper) for item in interval
            ]
            if not interval[0] <= number <= interval[1]:
                raise ValueError(f"{key} interval must contain its value")
        provenance[key] = dict(
            source_kind=source_kind, source=source, unit=unit, interval=interval
        )
    result["provenance"] = provenance
    result["validation"] = {
        "status": "unvalidated",
        "independently_verified": False,
        "real_world_validated": False,
        "scale_status": "operator_declared_metric"
        if metric_scale_declared
        else "unverified",
    }
    return result
