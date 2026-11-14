"""Compare independent observations and record finite numeric diagnostics."""

import math
from typing import Any


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
