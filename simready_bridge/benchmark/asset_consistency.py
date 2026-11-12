"""Category-independent comparison of supplied, source-indexed scene graphs.

IDs match only within one scene and survey scope. This does not perform object
recognition, infer hidden objects or authenticate the supplied annotations.
"""

import math

import numpy as np

from .contracts import finite, indexed, require

LEVELS = ("structure", "object", "part")
FIELDS = (
    "category",
    "level",
    "parent_id",
    "center_m",
    "size_m",
    "rotation_xyzw",
    "material_class",
    "state",
    "affordances",
)


def validate_policy(policy):
    require(isinstance(policy, dict), "Asset policy must be an object")
    for level, fields in policy.get("required_fields_by_level", {}).items():
        require(
            level in LEVELS
            and isinstance(fields, list)
            and len(fields) == len(set(fields))
            and all(f in FIELDS for f in fields),
            "Invalid required asset fields",
        )
    require(
        type(policy.get("relations_required", False)) is bool,
        "Invalid relations requirement",
    )


def _assets(graph):
    require(
        isinstance(graph, dict) and graph.get("schema_version") == 1,
        "Invalid asset graph schema",
    )
    for key in ("scene_id", "scope_id", "coordinate_frame"):
        require(isinstance(graph.get(key), str) and graph[key], f"Missing graph {key}")
    require(graph.get("units") == "m", "Asset graph units must be metres")
    require(
        type(graph.get("inventory_complete")) is bool,
        "Asset inventory completeness must be explicit",
    )
    rows = indexed(graph.get("assets"), "asset_id", "asset graph")
    for row in rows.values():
        require(
            isinstance(row.get("category"), str) and row["category"],
            "Missing asset category",
        )
        require(row.get("level") in LEVELS, "Invalid asset level")
        for name, count in (("center_m", 3), ("size_m", 3), ("rotation_xyzw", 4)):
            if row.get(name) is not None:
                value = row[name]
                require(
                    isinstance(value, list)
                    and len(value) == count
                    and all(finite(x) for x in value),
                    f"Invalid {name}",
                )
                if name == "size_m":
                    require(
                        all(x > 0 for x in value), "Asset dimensions must be positive"
                    )
                if name == "rotation_xyzw":
                    require(np.linalg.norm(value) > 1e-8, "Zero asset quaternion")
        if "parent_id" in row:
            require(
                row["parent_id"] is None or isinstance(row["parent_id"], str),
                "Invalid parent ID",
            )
        for name in ("material_class", "state"):
            if row.get(name) is not None:
                require(
                    isinstance(row[name], str) and row[name],
                    f"{name} must be a nonempty label",
                )
        if row.get("affordances") is not None:
            require(
                isinstance(row["affordances"], list)
                and all(isinstance(v, str) and v for v in row["affordances"])
                and len(set(row["affordances"])) == len(row["affordances"]),
                "Invalid affordance labels",
            )
    return rows


def _relations(graph):
    if "relations" not in graph:
        return None
    require(isinstance(graph["relations"], list), "Relations must be a list")
    rows = []
    for row in graph["relations"]:
        require(
            isinstance(row, dict)
            and all(
                isinstance(row.get(k), str) and row[k]
                for k in ("subject", "predicate", "object")
            ),
            "Invalid relation",
        )
        rows.append((row["subject"], row["predicate"], row["object"]))
    require(len(set(rows)) == len(rows), "Duplicate relation")
    return set(rows)


def asset_metrics(reference, predicted, policy=None):
    policy = policy or {}
    validate_policy(policy)
    expected, found = _assets(reference), _assets(predicted)
    require(bool(expected), "Empty source graph cannot certify asset fidelity")
    for key in ("scene_id", "scope_id", "coordinate_frame"):
        require(
            reference[key] == predicted[key],
            f"Asset graph {key} mismatch; register before comparing",
        )
    missing = sorted(set(expected) - set(found))
    unmatched = sorted(set(found) - set(expected))
    details = []
    errors = {}
    position = []
    size = []
    rotation = []
    unknown = 0
    missing_properties = 0

    def issue(asset_id, kind, **kw):
        details.append(dict(asset_id=asset_id, kind=kind, **kw))
        errors[kind] = errors.get(kind, 0) + 1

    for key in missing:
        issue(key, "missing_asset")
    for key in unmatched:
        issue(
            key,
            "extra_asset" if reference["inventory_complete"] else "unverified_asset",
        )
    for key, row in expected.items():
        fields = set(policy.get("required_fields_by_level", {}).get(row["level"], []))
        fields |= {f for f in FIELDS if f in row}
        fields |= {"category", "level"}
        for field in sorted(fields):
            # A null root parent is a valid annotation; other nulls are unknown.
            known = field in row and (row[field] is not None or field == "parent_id")
            if not known or row.get(field) == "unknown":
                unknown += 1
                issue(key, "unknown_source_property", field=field)
                continue
            if key not in found:
                continue
            actual = found[key]
            if (
                field not in actual
                or (actual[field] is None and field != "parent_id")
                or actual.get(field) == "unknown"
            ):
                missing_properties += 1
                issue(key, "missing_asset_property", field=field)
                continue
            a, b = row[field], actual[field]
            if field == "center_m":
                value = float(np.linalg.norm(np.array(a) - b))
                position.append(value)
                if value:
                    issue(key, "asset_position_error", error=value, unit="m")
            elif field == "size_m":
                value = float(np.max(np.abs(np.array(a) - b) / np.array(a)))
                size.append(value)
                if value:
                    issue(key, "asset_size_error", error=value, unit="relative")
            elif field == "rotation_xyzw":
                qa = np.array(a) / np.linalg.norm(a)
                qb = np.array(b) / np.linalg.norm(b)
                value = math.degrees(2 * math.acos(float(np.clip(abs(qa @ qb), 0, 1))))
                rotation.append(value)
                if value > 1e-6:
                    issue(key, "asset_orientation_error", error=value, unit="degrees")
            elif set(a) != set(b) if field == "affordances" else a != b:
                issue(
                    key,
                    {
                        "category": "wrong_asset_category",
                        "level": "wrong_asset_level",
                        "parent_id": "wrong_asset_parent",
                        "material_class": "wrong_asset_material",
                        "state": "wrong_asset_state",
                        "affordances": "wrong_asset_affordances",
                    }[field],
                    expected=a,
                    actual=b,
                )
    expected_rel, found_rel = _relations(reference), _relations(predicted)
    if expected_rel is not None:
        require(
            all(s in expected and o in expected for s, _, o in expected_rel),
            "Source relation has missing endpoint",
        )
    if found_rel is not None:
        require(
            all(s in found and o in found for s, _, o in found_rel),
            "Predicted relation has missing endpoint",
        )
    relation_error = None
    if expected_rel is not None and found_rel is not None:
        difference = expected_rel - found_rel
        # A partial inventory cannot establish that an unmatched relation is false.
        extras = found_rel - expected_rel if reference["inventory_complete"] else set()
        relation_error = len(difference) + len(extras)
        for s, p, o in sorted(difference):
            issue(s, "missing_asset_relation", predicate=p, object=o)
        for s, p, o in sorted(extras):
            issue(s, "extra_asset_relation", predicate=p, object=o)
    elif policy.get("relations_required"):
        if expected_rel is None:
            unknown += 1
            issue("scene", "unknown_source_property", field="relations")
        else:
            missing_properties += 1
            issue("scene", "missing_asset_property", field="relations")
    metrics = {
        "missing_count": len(missing),
        "unmatched_count": len(unmatched),
        "extra_count": len(unmatched) if reference["inventory_complete"] else None,
        "missing_critical_count": sum(
            expected[k].get("critical") is True for k in missing
        ),
        "unknown_source_property_count": unknown,
        "missing_property_count": missing_properties,
        "category_error_count": errors.get("wrong_asset_category", 0),
        "level_error_count": errors.get("wrong_asset_level", 0),
        "parent_error_count": errors.get("wrong_asset_parent", 0),
        "material_error_count": errors.get("wrong_asset_material", 0),
        "state_error_count": errors.get("wrong_asset_state", 0),
        "affordance_error_count": errors.get("wrong_asset_affordances", 0),
        "position_error_max_m": max(position) if position else None,
        "size_relative_error_max": max(size) if size else None,
        "orientation_error_max_deg": max(rotation) if rotation else None,
        "relation_error_count": relation_error,
        "inventory_complete": int(reference["inventory_complete"]),
    }
    return {
        "metrics": metrics,
        "details": details,
        "scene_id": reference["scene_id"],
        "scope_id": reference["scope_id"],
        "scope": "Supplied source graph comparison; no object detector, matching solver or physical proof",
        "unknown_source_properties_do_not_pass": True,
    }
