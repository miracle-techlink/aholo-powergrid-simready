"""Primitive input checks shared by domain contracts."""

import math


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def indexed(rows, key, label):
    require(isinstance(rows, list), f"{label} must be a list")
    require(
        all(
            isinstance(row, dict) and isinstance(row.get(key), str) and row[key]
            for row in rows
        ),
        f"{label} needs nonempty {key}",
    )
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), f"Duplicate {key} in {label}")
    return result


def number(value, name, minimum=0):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value
