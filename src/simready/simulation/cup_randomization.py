"""Reproducible mass/contact hypotheses for the prepared fixed-base cup task.

No controller, geometry, initial pose or acceptance threshold is randomized.
The resulting samples are simulation hypotheses, never material estimates.
"""

from __future__ import annotations

import math
import random
import shutil
from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, object_digest, write_json

PARAMETERS = {
    "cup_mass_kg": ("kg", 0.001, 2.0),
    "cup_static_friction": ("1", 0.01, 2.0),
    "gripper_static_friction": ("1", 0.01, 2.0),
    "table_static_friction": ("1", 0.01, 2.0),
    "dynamic_static_ratio": ("1", 0.0, 1.0),
}
ASSETS = ("task.usda", "plan.json", "robot.usdc", "cup.usdc")


def _number(value: Any, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Parameter bounds must be finite numbers")
    if not low <= value <= high:
        raise ValueError(f"Parameter outside supported range {low}..{high}")
    return float(value)


def validate_protocol(value: Any) -> dict[str, Any]:
    """Reject ambiguous units, unsupported changes and overlapping episode seeds."""
    keys = {"schema_version", "task", "source", "parameters", "episodes"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(
            "Protocol fields must be schema_version/task/source/parameters/episodes"
        )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["task"] != "fixed-base-cup-contact-v1"
    ):
        raise ValueError("Unsupported randomization protocol")
    if not isinstance(value["source"], str) or not value["source"].strip():
        raise ValueError("Declare the source and hypothetical nature of the ranges")
    parameters = value["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != set(PARAMETERS):
        raise ValueError("All five declared mass/contact parameters are required")
    for name, (unit, lower, upper) in PARAMETERS.items():
        spec = parameters[name]
        if not isinstance(spec, dict) or set(spec) != {"unit", "low", "high"}:
            raise ValueError(f"{name} requires unit, low and high")
        if spec["unit"] != unit:
            raise ValueError(f"Wrong unit for {name}")
        if _number(spec["low"], lower, upper) > _number(spec["high"], lower, upper):
            raise ValueError(f"Reversed range for {name}")
    episodes = value["episodes"]
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 128:
        raise ValueError("Declare 1..128 episodes")
    ids, seeds = set(), set()
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) != {"id", "split", "seed"}:
            raise ValueError("Episode requires id, split and seed")
        identifier = episode["id"]
        if (
            not isinstance(identifier, str)
            or not 1 <= len(identifier) <= 64
            or any(
                c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in identifier
            )
            or identifier in ids
        ):
            raise ValueError("Episode id must be a unique safe lowercase identifier")
        seed = episode["seed"]
        if type(seed) is not int or not 0 <= seed < 2**63 or seed in seeds:
            raise ValueError("Episode seeds must be unique, including across splits")
        if episode["split"] not in {"development", "heldout"}:
            raise ValueError("Split must be development or heldout")
        ids.add(identifier)
        seeds.add(seed)
    return value


def sample_protocol(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    """Sample each named parameter independently of episode/dictionary order."""
    validate_protocol(protocol)
    samples = []
    for episode in protocol["episodes"]:
        values = {}
        for name in sorted(PARAMETERS):
            seed = int(object_digest({"seed": episode["seed"], "parameter": name}), 16)
            bounds = protocol["parameters"][name]
            values[name] = random.Random(seed).uniform(bounds["low"], bounds["high"])
        samples.append({**episode, "parameters": values})
    return samples


def prepare_episode(
    assets: str | Path,
    output: str | Path,
    sample: dict[str, Any],
    protocol_sha256: str,
) -> dict[str, Any]:
    """Copy portable task assets and author per-contact materials before stepping."""
    from pxr import Gf, Usd, UsdPhysics, UsdShade

    assets, output = Path(assets).resolve(), Path(output).resolve()
    if output == assets or assets in output.parents:
        raise ValueError("Episode output must be outside the source assets")
    parameters = sample["parameters"]
    if set(parameters) != set(PARAMETERS):
        raise ValueError("Unexpected sampled parameters")
    for name, (_, lower, upper) in PARAMETERS.items():
        _number(parameters[name], lower, upper)
    source_hashes = {name: digest(assets / name) for name in ASSETS}
    output.mkdir(parents=True, exist_ok=False)
    for name in ASSETS:
        shutil.copy2(assets / name, output / name)
    stage = Usd.Stage.Open(str(output / "task.usda"))
    if not stage:
        raise ValueError("Cannot open prepared task USD")
    cup = stage.GetPrimAtPath("/World/Cup")
    if not cup or not cup.HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError("Prepared dynamic cup is missing")
    mass = UsdPhysics.MassAPI(cup)
    previous_mass = float(mass.GetMassAttr().Get())
    if not math.isfinite(previous_mass) or previous_mass <= 0:
        raise ValueError("Original cup mass must be positive")
    previous_inertia = list(mass.GetDiagonalInertiaAttr().Get())
    if not all(math.isfinite(v) and v >= 0 for v in previous_inertia):
        raise ValueError("Unsupported original cup inertia")
    if any(previous_inertia) and not all(v > 0 for v in previous_inertia):
        raise ValueError("Partially automatic inertia is unsupported")
    new_mass = parameters["cup_mass_kg"]
    mass.CreateMassAttr(new_mass)
    inertia_mode = "engine_recomputed_from_unchanged_geometry"
    if all(v > 0 for v in previous_inertia):
        inertia_mode = "explicit_diagonal_scaled_with_mass"
        mass.CreateDiagonalInertiaAttr(
            Gf.Vec3f(*(v * new_mass / previous_mass for v in previous_inertia))
        )
    ratio = parameters["dynamic_static_ratio"]
    groups: dict[str, list[str]] = {"cup": [], "gripper": [], "table": []}
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        path = str(prim.GetPath())
        if path == "/World/Cup" or path.startswith("/World/Cup/"):
            groups["cup"].append(path)
        elif path.startswith("/World/G2/gripper_r_"):
            groups["gripper"].append(path)
        elif path in {"/World/Table", "/World/TableB"}:
            groups["table"].append(path)
    if not all(groups.values()) or len(groups["table"]) != 2:
        raise ValueError("Need cup, right-gripper collisions and both support tables")
    bindings = {}
    for name, paths in groups.items():
        path = f"/World/RandomizedContact/{name}"
        material = UsdShade.Material.Define(stage, path)
        api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        friction = parameters[f"{name}_static_friction"]
        api.CreateStaticFrictionAttr(friction)
        api.CreateDynamicFrictionAttr(friction * ratio)
        api.CreateRestitutionAttr(0)
        # PhysX schema is registered in the Isaac worker; author the standard
        # token names on the host and verify them again from composed USD.
        from pxr import Sdf

        for key in ("friction", "restitution"):
            material.GetPrim().CreateAttribute(
                f"physxMaterial:{key}CombineMode", Sdf.ValueTypeNames.Token
            ).Set("average")
        material.GetPrim().AddAppliedSchema("PhysxMaterialAPI")
        for target in paths:
            UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(target)).Bind(
                material,
                bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                materialPurpose="physics",
            )
        bindings[name] = {"material": path, "collisions": paths}
    stage.GetRootLayer().Save()
    del stage
    if any(digest(assets / name) != value for name, value in source_hashes.items()):
        raise ValueError("Source assets changed during episode preparation")
    receipt = {
        "schema_version": 1,
        "sample": sample,
        "protocol_sha256": protocol_sha256,
        "source_assets_sha256": source_hashes,
        "episode_assets_sha256": {name: digest(output / name) for name in ASSETS},
        "bindings": bindings,
        "mass_before_kg": previous_mass,
        "inertia_before_kg_m2": previous_inertia,
        "inertia_mode": inertia_mode,
        "controller_unchanged": True,
        "plan_sha256": digest(output / "plan.json"),
        "plan_mass_note": "plan.json cup.mass_kg is the historical nominal value; composed USD mass is authoritative for this episode",
        "material_combine": "average on both sides; effective pair coefficient is their arithmetic mean",
        "real_world_validated": False,
    }
    write_json(output / "randomization.json", receipt)
    return receipt
