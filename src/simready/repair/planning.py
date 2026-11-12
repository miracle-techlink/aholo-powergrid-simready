"""Deterministic plugin planning. No unchecked legacy script is auto-executed."""

from simready.core.artifacts import object_digest as fingerprint
from simready.core.types import JsonObject
from simready.core.validation import indexed, require


def plan_repairs(
    defect_bundle: JsonObject,
    registry: JsonObject,
    *,
    max_actions: int = 4,
    round_index: int = 0,
    max_rounds: int = 4,
    stagnant_rounds: int = 0,
) -> JsonObject:
    """Produce a nonexecuting plan from defects and the adapter capability catalog."""
    require(
        defect_bundle.get("phase") == "development",
        "Sealed results cannot drive repair planning",
    )
    require(
        type(max_actions) is int and max_actions > 0, "max_actions must be positive"
    )
    require(type(round_index) is int and round_index >= 0, "Invalid round index")
    require(type(max_rounds) is int and max_rounds > 0, "Invalid round budget")
    require(
        type(stagnant_rounds) is int and stagnant_rounds >= 0,
        "Invalid stagnation count",
    )
    plugins = indexed(registry.get("plugins"), "plugin_id", "plugin registry")
    indexed(defect_bundle.get("defects"), "defect_id", "defects")
    if round_index >= max_rounds or stagnant_rounds >= 2:
        return {
            "status": "BUDGET_EXHAUSTED"
            if round_index >= max_rounds
            else "NEEDS_NEW_STRATEGY",
            "actions": [],
            "executed": False,
        }
    groups, blocked = {}, []
    for defect in defect_bundle["defects"]:
        if not defect["repairable_now"]:
            blocked.append(
                {
                    "defect_id": defect["defect_id"],
                    "reason": defect["reason"],
                    "next_action": "CALIBRATE"
                    if defect["reason"] == "THRESHOLD_NOT_CALIBRATED"
                    else "COLLECT_EVIDENCE",
                }
            )
            continue
        matches = [p for p in plugins.values() if defect["kind"] in p["handles"]]
        if not matches:
            blocked.append({"defect_id": defect["defect_id"], "reason": "NO_PLUGIN"})
            continue
        plugin = min(matches, key=lambda x: (x["priority"], x["plugin_id"]))
        entry = groups.setdefault(
            plugin["plugin_id"],
            {
                "plugin_id": plugin["plugin_id"],
                "priority": plugin["priority"],
                "defect_ids": [],
                "asset_ids": [],
                "invalidates": plugin["invalidates"],
                "requires": plugin["requires"],
                "writes": plugin["writes"],
                "adapter_status": plugin["adapter_status"],
                "execution_status": "ADAPTER_REQUIRED",
                "promotion_requires": "Independent local and global regression",
            },
        )
        entry["defect_ids"].append(defect["defect_id"])
        entry["asset_ids"] = sorted(set(entry["asset_ids"]) | set(defect["asset_ids"]))
    proposed = sorted(groups.values(), key=lambda x: (x["priority"], x["plugin_id"]))
    actions, deferred, occupied = [], [], set()
    for entry in proposed:
        # Conservative: overlapping write domains serialize even across assets.
        conflicts = set(entry["writes"]) & occupied
        if conflicts or len(actions) >= max_actions:
            deferred.append(
                {**entry, "reason": "WRITE_CONFLICT" if conflicts else "ACTION_BUDGET"}
            )
            continue
        occupied.update(entry["writes"])
        entry["idempotency_key"] = fingerprint(
            {
                "protocol": defect_bundle["protocol_sha256"],
                "candidate": defect_bundle["candidate_sha256"],
                "action": entry,
            }
        )
        actions.append(entry)
    return {
        "schema_version": 1,
        "status": "PLAN_ONLY"
        if actions
        else "NEEDS_EVIDENCE"
        if blocked
        else "NO_OPEN_DEFECTS",
        "protocol_sha256": defect_bundle["protocol_sha256"],
        "candidate_sha256": defect_bundle["candidate_sha256"],
        "actions": actions,
        "blocked": blocked,
        "deferred": deferred,
        "executed": False,
        "round_index": round_index,
        "max_rounds": max_rounds,
        "note": "Adapter contracts must be implemented before execution; registered script paths are hints only.",
    }
