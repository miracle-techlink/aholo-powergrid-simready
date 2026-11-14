"""Independent readback and failure-inclusive statistics for cup campaigns."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, read_json

_TASK_ASSETS = {"task.usda", "plan.json", "robot.usdc", "cup.usdc"}
_TASK_CHECKS = {
    "evidence_hashes_match",
    "complete_phase_sequence",
    "finite_measured_arrays",
    "minimum_2s_hold",
    "lifted_clear_of_table",
    "bilateral_hold_and_transfer",
    "upright_during_transfer",
    "destination_center_error_under_15mm",
    "destination_surface_contact",
    "upright_after_release",
    "released_from_gripper",
    "supported_by_destination_table",
    "robot_did_not_hit_monitored_environment",
    "no_object_attachment_joint",
    "object_is_dynamic",
}
_PARAMETER_CHECKS = {
    "composition_complete",
    "frozen_sample_matches",
    "asset_hashes_match",
    "plan_unchanged",
    "dependencies_unchanged",
    "mass_matches",
    "mass_inertia_consistent",
    *(
        f"{group}.{check}"
        for group in ("cup", "gripper", "table")
        for check in (
            "coefficients",
            "combine",
            "coverage",
            "binding",
            "collisions_enabled",
        )
    ),
}


def _passing_audit(value: Any, required: set[str]) -> bool:
    """A status label without its successful checks is not an accepted audit."""
    return (
        isinstance(value, dict)
        and value.get("status") == "PASS"
        and isinstance(value.get("checks"), dict)
        and bool(value["checks"])
        and required <= value["checks"].keys()
        and all(item is True for item in value["checks"].values())
    )


def execution_status(exit_code: int, worker: dict, audit: dict) -> str:
    """Distinguish complete task failures from crashes or truncated rollouts.

    Isaac's shell wrapper maps task-failure exit 2 to exit 1. Complete failed
    tasks stay in the statistics; a nonzero exit never counts as task success.
    """
    complete = (
        exit_code in (0, 1, 2)
        and worker.get("status") in {"PASS", "FAILED"}
        and not worker.get("error")
        and worker.get("frames", 0) > 0
        and bool(worker.get("checks"))
        and all(
            audit.get("checks", {}).get(key) is True
            for key in (
                "complete_phase_sequence",
                "finite_measured_arrays",
                "evidence_hashes_match",
            )
        )
        and (exit_code == 0 or worker["status"] == "FAILED")
    )
    return "COMPLETE" if complete else "WORKER_FAILED"


def inspect_parameters(
    assets: str | Path,
    expected: dict[str, Any],
    *,
    source_assets_sha256: dict[str, str] | None = None,
    protocol_sha256: str | None = None,
) -> dict[str, Any]:
    """Read composed values and bindings; do not import the USD authoring code."""
    from pxr import Usd, UsdPhysics, UsdShade

    assets = Path(assets)
    receipt = read_json(assets / "randomization.json")
    stage = Usd.Stage.Open(str(assets / "task.usda"))
    if not stage:
        raise ValueError("Cannot read randomized task")
    if (
        receipt.get("schema_version") != 1
        or set(receipt.get("episode_assets_sha256", {})) != _TASK_ASSETS
        or set(receipt.get("source_assets_sha256", {})) != _TASK_ASSETS
    ):
        raise ValueError(
            "Randomization receipt requires the complete task asset inventory"
        )
    checks = {
        "composition_complete": not stage.GetCompositionErrors(),
        "frozen_sample_matches": receipt["sample"] == expected,
        "asset_hashes_match": all(
            digest(assets / name) == sha
            for name, sha in receipt["episode_assets_sha256"].items()
        ),
        "plan_unchanged": digest(assets / "plan.json")
        == receipt["source_assets_sha256"]["plan.json"],
        "dependencies_unchanged": all(
            digest(assets / name) == receipt["source_assets_sha256"][name]
            for name in ("robot.usdc", "cup.usdc")
        ),
    }
    if source_assets_sha256 is not None:
        checks["frozen_source_assets_match"] = (
            receipt["source_assets_sha256"] == source_assets_sha256
        )
    if protocol_sha256 is not None:
        checks["frozen_protocol_matches"] = (
            receipt["protocol_sha256"] == protocol_sha256
        )
    params = expected["parameters"]
    cup = stage.GetPrimAtPath("/World/Cup")
    api = UsdPhysics.MassAPI(cup)
    mass = float(api.GetMassAttr().Get())
    checks["mass_matches"] = math.isclose(mass, params["cup_mass_kg"], rel_tol=1e-6)
    original = receipt["inertia_before_kg_m2"]
    expected_inertia = (
        [v * params["cup_mass_kg"] / receipt["mass_before_kg"] for v in original]
        if all(v > 0 for v in original)
        else [0.0, 0.0, 0.0]
    )
    inertia = list(api.GetDiagonalInertiaAttr().Get())
    checks["mass_inertia_consistent"] = all(
        math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-10)
        for a, b in zip(inertia, expected_inertia, strict=True)
    )
    values = {}
    for group in ("cup", "gripper", "table"):
        binding = receipt["bindings"][group]
        material = stage.GetPrimAtPath(binding["material"])
        api = UsdPhysics.MaterialAPI(material)
        static = float(api.GetStaticFrictionAttr().Get())
        dynamic = float(api.GetDynamicFrictionAttr().Get())
        friction = params[group + "_static_friction"]
        checks[group + ".coefficients"] = (
            math.isclose(static, friction, rel_tol=1e-6)
            and math.isclose(
                dynamic,
                friction * params["dynamic_static_ratio"],
                rel_tol=1e-6,
                abs_tol=1e-8,
            )
            and float(api.GetRestitutionAttr().Get()) == 0
            and 0 <= dynamic <= static
        )
        checks[group + ".combine"] = (
            "PhysxMaterialAPI" in material.GetMetadata("apiSchemas").GetAppliedItems()
            and material.GetAttribute("physxMaterial:frictionCombineMode").Get()
            == "average"
            and material.GetAttribute("physxMaterial:restitutionCombineMode").Get()
            == "average"
        )
        paths = []
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            path = str(prim.GetPath())
            matches = (
                (
                    group == "cup"
                    and (path == "/World/Cup" or path.startswith("/World/Cup/"))
                )
                or (group == "gripper" and path.startswith("/World/G2/gripper_r_"))
                or (group == "table" and path in {"/World/Table", "/World/TableB"})
            )
            if matches:
                paths.append(path)
        checks[group + ".coverage"] = (
            bool(paths)
            and set(paths) == set(binding["collisions"])
            and (group != "table" or len(paths) == 2)
        )
        checks[group + ".collisions_enabled"] = all(
            UsdPhysics.CollisionAPI(stage.GetPrimAtPath(path))
            .GetCollisionEnabledAttr()
            .Get()
            is True
            for path in paths
        )
        checks[group + ".binding"] = all(
            str(
                UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path))
                .ComputeBoundMaterial(materialPurpose="physics")[0]
                .GetPath()
            )
            == binding["material"]
            for path in paths
        )
        values[group] = {"static_friction": static, "dynamic_friction": dynamic}
    return {
        "status": "PASS" if all(checks.values()) else "FAILED",
        "checks": checks,
        "cup_mass_kg": mass,
        "materials": values,
        "effective_pair_dynamic_friction": {
            pair: (
                values["cup"]["dynamic_friction"] + values[other]["dynamic_friction"]
            )
            / 2
            for pair, other in (("cup_gripper", "gripper"), ("cup_table", "table"))
        },
        "real_parameter_accuracy": "NOT_EVALUATED",
    }


def summarize_campaign(
    manifest: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep missing/crashed/not-run episodes in the denominator, with Wilson CI."""
    expected = {item["id"]: item for item in manifest["samples"]}
    if not expected or len(expected) != len(manifest["samples"]):
        raise ValueError("Campaign needs unique predeclared samples")
    indexed = {}
    for row in records:
        key = row.get("id")
        if key not in expected or key in indexed:
            raise ValueError("Unexpected or duplicate episode result")
        indexed[key] = row
    rows = []
    for key, sample in expected.items():
        row = indexed.get(key, {"id": key, "status": "NOT_RUN"})
        success = (
            row.get("status") == "COMPLETE"
            and row.get("worker_exit_code") == 0
            and row.get("worker_status") == "PASS"
            and row.get("task_audit_exit_code") == 0
            and row.get("runtime_parameters_match") is True
            and _passing_audit(row.get("parameter_audit"), _PARAMETER_CHECKS)
            and _passing_audit(row.get("task_audit"), _TASK_CHECKS)
        )
        rows.append({**row, "split": sample["split"], "success": success})

    def summary(selected):
        count = len(selected)
        successes = sum(row["success"] for row in selected)
        if not count:
            return {
                "episodes": 0,
                "successes": 0,
                "success_rate": None,
                "wilson_95": None,
            }
        p, z = successes / count, 1.959963984540054
        center = (p + z * z / (2 * count)) / (1 + z * z / count)
        half = (
            z
            * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count))
            / (1 + z * z / count)
        )
        return {
            "episodes": count,
            "successes": successes,
            "success_rate": p,
            "wilson_95": [max(0, center - half), min(1, center + half)],
            "execution_failures_or_missing": sum(
                row.get("status") != "COMPLETE" for row in selected
            ),
        }

    return {
        "schema_version": 1,
        "status": "COMPLETE"
        if all(row.get("status") == "COMPLETE" for row in rows)
        else "INCOMPLETE",
        "scope": "fixed controller, fixed-base prepared two-table cup task; mass/contact hypotheses only",
        "all": summary(rows),
        "by_split": {
            name: summary([r for r in rows if r["split"] == name])
            for name in ("development", "heldout")
        },
        "episodes": rows,
        "confidence_interval_scope": "Wilson binomial interval for the declared synthetic parameter distribution; not real-world reliability",
        "controller_optimization_performed": False,
        "policy_training_performed": False,
        "real_world_validated": False,
    }
