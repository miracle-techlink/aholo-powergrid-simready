"""Fixed synthetic PhysX response probe; never a real-material calibration.

Run inside Isaac Sim's Python: ``python -m simready.simulation.physical_probe
--out /output/probe``. The versioned protocol uses a CPU PhysX memory stage in
one headless Kit process, without rendering or robot assets. It checks that
authored friction and mass influence trajectories, not that their values match
any real object or that GPU/robot behavior is equivalent.
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import math
import os
import platform
import random
import time
from pathlib import Path
from typing import Any

from simready.assets.physics.physical_authoring import (
    author_physics_metadata,
    author_rigid_body,
    bind_contact_material,
)
from simready.assets.physics.physical_spec import normalize_physics
from simready.core.artifacts import digest, write_json

PROTOCOL = {
    "version": "synthetic-physical-response-v1",
    "dt_s": 1 / 120,
    "steps": 240,
    "seed": 20261113,
    "randomization": "none; all geometry and initial states fixed",
    "gravity_m_s2": 9.81,
    "backend": "CPU PhysX; headless Isaac Sim; no render calls in physics loop",
    "solver": "TGS",
    "material_combine": "average; each slider and its floor use identical coefficients",
    "low_friction": 0.05,
    "high_friction": 0.8,
    "initial_slider_velocity_m_s": 2.0,
    "force_N": 1.0,
    "light_mass_kg": 1.0,
    "heavy_mass_kg": 4.0,
    "source": "Versioned synthetic software fixture; no measured material parameters",
}


def evaluate_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate complete sampled trajectories against fixed protocol tolerances."""
    names = ("low_friction", "high_friction", "light_mass", "heavy_mass")
    expected_steps = PROTOCOL["steps"] + 1
    checks: list[dict[str, Any]] = []
    series = {}
    for name in names:
        samples = [row for row in rows if row.get("body") == name]
        valid = len(samples) == expected_steps
        if valid:
            for step, row in enumerate(samples):
                values = [row.get(key) for key in ("t_s", "x_m", "y_m", "z_m")]
                valid = (
                    valid
                    and row.get("step") == step
                    and all(
                        type(value) in (int, float) and math.isfinite(value)
                        for value in values
                    )
                )
                if valid:
                    valid = abs(row["t_s"] - step * PROTOCOL["dt_s"]) < 1e-9
        checks.append(
            {"name": name + ".complete_finite_trajectory", "passed": bool(valid)}
        )
        series[name] = samples
    if not all(check["passed"] for check in checks):
        return {"status": "FAILED", "checks": checks, "metrics": {}}
    displacement = {
        name: values[-1]["x_m"] - values[0]["x_m"] for name, values in series.items()
    }
    low, high = displacement["low_friction"], displacement["high_friction"]
    duration = PROTOCOL["steps"] * PROTOCOL["dt_s"]
    analytical = {
        "light_mass": 0.5
        * PROTOCOL["force_N"]
        / PROTOCOL["light_mass_kg"]
        * duration**2,
        "heavy_mass": 0.5
        * PROTOCOL["force_N"]
        / PROTOCOL["heavy_mass_kg"]
        * duration**2,
    }
    checks.extend(
        [
            {
                "name": "low_friction_travels_farther",
                "passed": low > 1 and 0 <= high < 1 and low > high * 3 + 0.1,
            },
            {
                "name": "sliders_remain_on_support",
                "passed": all(
                    abs(series[name][-1]["z_m"] - 0.1) < 0.03 for name in names[:2]
                ),
            },
        ]
    )
    for name, expected in analytical.items():
        checks.append(
            {
                "name": name + ".force_over_mass_response",
                "passed": abs(displacement[name] - expected) <= expected * 0.05,
            }
        )
    ratio = displacement["light_mass"] / max(displacement["heavy_mass"], 1e-12)
    checks.append(
        {"name": "inverse_mass_displacement_ratio", "passed": abs(ratio - 4.0) <= 0.2}
    )
    return {
        "status": "PASS" if all(item["passed"] for item in checks) else "FAILED",
        "checks": checks,
        "metrics": {
            "displacement_x_m": displacement,
            "analytical_constant_force_displacement_m": analytical,
            "light_heavy_displacement_ratio": ratio,
        },
    }


def _box(
    stage: Any,
    name: str,
    center: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    *,
    mass: float | None,
    friction: float,
) -> Any:
    from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

    path = "/World/" + name
    root = UsdGeom.Xform.Define(stage, path)
    root.AddTranslateOp().Set(Gf.Vec3d(*center))
    shape = UsdGeom.Cube.Define(stage, path + "/Shape")
    shape.CreateSizeAttr(1)
    shape.AddScaleOp().Set(Gf.Vec3f(*dimensions))
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    value = {
        "motion_type": "static" if mass is None else "dynamic",
        "contact": {
            "static_friction": friction,
            "dynamic_friction": friction,
            "restitution": 0,
        },
    }
    if mass is not None:
        value["mass_kg"] = mass
    physics = normalize_physics(
        value, geometry_type="box", fallback_contact={}, metric_scale_declared=True
    )
    author_rigid_body(root.GetPrim(), physics)
    author_physics_metadata(root.GetPrim(), physics)
    material_path = bind_contact_material(
        stage, path + "/Material", [path + "/Shape"], physics["contact"]
    )
    material_api = PhysxSchema.PhysxMaterialAPI.Apply(
        stage.GetPrimAtPath(material_path)
    )
    material_api.CreateFrictionCombineModeAttr("average")
    material_api.CreateRestitutionCombineModeAttr("average")
    if mass is not None:
        body = PhysxSchema.PhysxRigidBodyAPI.Apply(root.GetPrim())
        body.CreateLinearDampingAttr(0)
        body.CreateAngularDampingAttr(0)
    return root.GetPrim()


def run_probe(output: str | Path) -> dict[str, Any]:
    """Run the frozen protocol and preserve raw trajectories, USD and timings."""
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "RUNNING",
        "protocol": dict(PROTOCOL),
        "real_world_calibration": "NOT_EVALUATED",
        "robot_task_validation": "NOT_EVALUATED",
        "timings_s": {},
        "source_sha256": digest(__file__),
        "timing_scope": "Worker through completed physics/artifact writes; process shutdown is timed by the outer launcher.",
        "code_sha256": {
            name: digest(Path(__file__).with_name(name))
            for name in ("physical_spec.py", "physical_authoring.py")
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    write_json(out / "probe.json", result)
    app, stage = None, None
    try:
        from isaacsim import SimulationApp

        random.seed(PROTOCOL["seed"])
        try:
            result["runtime"]["isaacsim"] = importlib.metadata.version("isaacsim")
            result["runtime"]["version_source"] = "Python distribution metadata"
        except importlib.metadata.PackageNotFoundError:
            version_file = Path(os.environ["EXP_PATH"]).parent / "VERSION"
            result["runtime"]["isaacsim"] = (
                version_file.read_text().strip()
                if version_file.is_file()
                else "unknown"
            )
            result["runtime"]["version_source"] = str(version_file)
        tick = time.perf_counter()
        app = SimulationApp(
            {"headless": True},
            experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
        )
        result["timings_s"]["kit_startup"] = time.perf_counter() - tick
        import carb.settings
        import omni.physx
        from omni.physx.scripts.utils import new_memory_stage, release_memory_stage
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

        tick = time.perf_counter()
        carb.settings.get_settings().set_bool("/physics/updateToUsd", True)
        carb.settings.get_settings().set_bool("/physics/updateVelocitiesToUsd", True)
        stage = new_memory_stage(attach_stage=False)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
        world = UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(world.GetPrim())
        scene = UsdPhysics.Scene.Define(stage, "/World/Physics")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(PROTOCOL["gravity_m_s2"])
        physx_scene = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
        physx_scene.CreateEnableGPUDynamicsAttr(False)
        physx_scene.CreateBroadphaseTypeAttr("MBP")
        physx_scene.CreateSolverTypeAttr(PROTOCOL["solver"])
        physx_scene.CreateEnableEnhancedDeterminismAttr(True)
        bodies = {}
        for name, y, friction in [
            ("low_friction", -3, PROTOCOL["low_friction"]),
            ("high_friction", 0, PROTOCOL["high_friction"]),
        ]:
            _box(
                stage,
                name + "_floor",
                (3, y, -0.1),
                (14, 1, 0.2),
                mass=None,
                friction=friction,
            )
            prim = _box(
                stage, name, (0, y, 0.105), (0.2, 0.2, 0.2), mass=1, friction=friction
            )
            UsdPhysics.RigidBodyAPI(prim).CreateVelocityAttr(
                Gf.Vec3f(PROTOCOL["initial_slider_velocity_m_s"], 0, 0)
            )
            bodies[name] = prim
        for name, y, mass in [
            ("light_mass", 3, PROTOCOL["light_mass_kg"]),
            ("heavy_mass", 5, PROTOCOL["heavy_mass_kg"]),
        ]:
            prim = _box(stage, name, (0, y, 1), (0.2, 0.2, 0.2), mass=mass, friction=0)
            PhysxSchema.PhysxRigidBodyAPI(prim).CreateDisableGravityAttr(True)
            force = PhysxSchema.PhysxForceAPI.Apply(prim)
            force.CreateForceAttr(Gf.Vec3f(PROTOCOL["force_N"], 0, 0))
            force.CreateWorldFrameEnabledAttr(True)
            force.CreateModeAttr("force")
            bodies[name] = prim
        stage.GetRootLayer().Export(str(out / "initial_scene.usda"))
        from pxr import UsdUtils

        simulation = omni.physx.get_physx_simulation_interface()
        simulation.attach_stage(UsdUtils.StageCache.Get().GetId(stage).ToLongInt())
        result["timings_s"]["scene_build_and_attach"] = time.perf_counter() - tick
        tick, rows = time.perf_counter(), []
        for step in range(PROTOCOL["steps"] + 1):
            if step:
                simulation.simulate(PROTOCOL["dt_s"], (step - 1) * PROTOCOL["dt_s"])
                simulation.fetch_results()
            for name, prim in bodies.items():
                position = (
                    UsdGeom.Xformable(prim)
                    .ComputeLocalToWorldTransform(0)
                    .ExtractTranslation()
                )
                rows.append(
                    {
                        "step": step,
                        "t_s": step * PROTOCOL["dt_s"],
                        "body": name,
                        "x_m": float(position[0]),
                        "y_m": float(position[1]),
                        "z_m": float(position[2]),
                    }
                )
        result["timings_s"]["physics_and_sampling"] = time.perf_counter() - tick
        tick = time.perf_counter()
        with (out / "trajectories.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result.update(evaluate_response(rows))
        result["files"] = {
            name: {"path": name, "sha256": digest(out / name)}
            for name in ("initial_scene.usda", "trajectories.csv")
        }
        result["timings_s"]["analysis_and_artifacts"] = time.perf_counter() - tick
        release_memory_stage(stage)
        stage = None
    except Exception as error:
        result.update(status="FAILED", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        # Persist the dynamics outcome before shutdown, including SDK errors.
        result["timings_s"]["before_shutdown_wall"] = time.perf_counter() - started
        write_json(out / "probe.json", result)
        print(json.dumps(result, indent=2), flush=True)
        if app is not None:
            tick = time.perf_counter()
            # Isaac 6.1 fast shutdown terminates the interpreter. Preserve the
            # failed outcome instead of masking it with the SDK's default 0.
            app.close(exit_code=0 if result["status"] == "PASS" else 2)
            result["timings_s"]["kit_shutdown"] = time.perf_counter() - tick
        result["timings_s"]["total_wall"] = time.perf_counter() - started
        write_json(out / "probe.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    """Return success only after actual complete trajectories pass fixed checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_probe(args.out)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
