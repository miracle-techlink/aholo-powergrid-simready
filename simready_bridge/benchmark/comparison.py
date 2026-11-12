"""Conservative candidate promotion under an unchanged development protocol."""

import math


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def compare(before, after, epsilon=1e-9):
    if not finite(epsilon) or epsilon < 0:
        raise ValueError("Comparison epsilon must be finite and nonnegative")
    if before["protocol_sha256"] != after["protocol_sha256"]:
        raise ValueError(
            "Cannot compare candidates evaluated under different protocols"
        )
    old = {row["check_id"]: row for row in before["checks"]}
    new = {row["check_id"]: row for row in after["checks"]}
    if len(old) != len(before["checks"]) or len(new) != len(after["checks"]):
        raise ValueError("Duplicate verifier check")
    if set(old) != set(new):
        raise ValueError("Verifier check set changed")
    regressions, improvements = [], []
    for key, a in old.items():
        b = new[key]
        if any(
            a.get(field) != b.get(field)
            for field in ("required", "metric", "op", "limit")
        ):
            raise ValueError("Verifier check definition changed")
        if not a["required"]:
            continue
        if a["status"] == "PASS" and b["status"] != "PASS":
            regressions.append(key)
        if a["status"] != "PASS" and b["status"] == "PASS":
            improvements.append(key)
        x, y = a.get("value"), b.get("value")
        if finite(x) and not finite(y):
            regressions.append(key)
        elif finite(x) and finite(y):
            if a["op"] not in (">=", "<=", "==") or not finite(a.get("limit")):
                raise ValueError("Invalid numeric verifier check definition")
            delta = (
                y - x
                if a["op"] == ">="
                else x - y
                if a["op"] == "<="
                else abs(x - a["limit"]) - abs(y - a["limit"])
            )
            if delta < -epsilon:
                regressions.append(key)
            elif delta > epsilon:
                improvements.append(key)
    accepted = not regressions and bool(improvements)
    return {
        "accepted": accepted,
        "regressions": sorted(set(regressions)),
        "improvements": sorted(set(improvements)),
        "reason": "REGRESSION"
        if regressions
        else "IMPROVEMENT"
        if improvements
        else "NO_PROGRESS",
        "candidate_passed": after["status"] == "PASS",
        "release_authorized": False,
    }
