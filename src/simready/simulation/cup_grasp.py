"""Fixed-base cup entrypoint: lifecycle, versioned commands and measured evidence.

The baseline remains the default. Candidate feedback is explicit and must pass
the independent audit in new paired physical trials before any promotion.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import time
import traceback
from pathlib import Path

from simready.core.artifacts import write_json
from simready.simulation.cup_control import VERSIONS, CupController, controller_spec
from simready.simulation.cup_recording import CupRecorder, implementation_hashes
from simready.simulation.cup_runtime import IsaacCupRuntime


def run_task(args, *, runtime_factory=IsaacCupRuntime):
    """Persist initialization/step failures and close partially opened resources."""
    started = time.perf_counter()
    result = {
        "status": "FAILED",
        "scope": "fixed-base contact canary; known target",
        "controller": controller_spec(args.controller),
        "controller_code_sha256": implementation_hashes(),
    }
    runtime = runtime_factory(args, result)
    recorder = controller = None
    pending = None
    try:
        runtime.start()
        controller = CupController(
            runtime.plan,
            runtime.names,
            physics_hz=args.physics_hz,
            closed=args.gripper_close_rad,
            version=args.controller,
            mass_kg=float(runtime.cup.get_masses().reshape(-1)[0]),
        )
        recorder = CupRecorder(args.out, runtime.dt)
        observation = runtime.observe()
        for phase in controller.phases:
            controller.begin_phase(phase, observation, runtime)
            print("PHASE", phase[0], flush=True)
            for step in range(controller.steps):
                command = controller.command(step, observation, runtime)
                runtime.step(command, (recorder.frames + 1) * runtime.dt)
                observation = runtime.observe()
                recorder.append(observation, command, phase[0])
            print(
                "STATE",
                phase[0],
                "cup",
                observation["poses"][-1].tolist(),
                "force",
                observation["forces"].tolist(),
                flush=True,
            )
        recorder.finish(result, runtime.plan, runtime)
        runtime.export(args.out / "final_stage.usda")
    except BaseException as error:
        pending = error
        traceback.print_exc()
        result.update(
            status="FAILED" if isinstance(error, Exception) else "INTERRUPTED",
            error=f"{type(error).__name__}: {error}",
        )
    finally:
        cleanup = []

        def finalize(resource, action):
            nonlocal pending
            try:
                return action()
            except BaseException as error:
                cleanup.append({"resource": resource, "error": type(error).__name__})
                if pending is None or (
                    isinstance(pending, Exception) and not isinstance(error, Exception)
                ):
                    pending = error
                result.setdefault("error", f"{type(error).__name__}: {error}")
                result["status"] = (
                    "FAILED" if isinstance(pending, Exception) else "INTERRUPTED"
                )
                return None

        if controller is not None:
            result["placement_feedback"] = controller.placement_feedback
            finalize(
                "controller_events",
                lambda: write_json(
                    args.out / "controller_events.json", controller.events
                ),
            )
        if recorder is not None and "rollout_sha256" not in result:
            finalize("partial_rollout", lambda: recorder.save(result))
        cleanup.extend(finalize("physics_cleanup", runtime.close_physics) or [])
        result["cleanup_errors"] = cleanup
        if cleanup:
            result["status"] = (
                "FAILED"
                if pending is None or isinstance(pending, Exception)
                else "INTERRUPTED"
            )
        result["wall_s"] = time.perf_counter() - started
        finalize(
            "result_stdout", lambda: print(json.dumps(result, indent=2), flush=True)
        )
        finalize("result_receipt", lambda: write_json(args.out / "result.json", result))
        code = 0 if result["status"] == "PASS" else 2
        previous_errors = len(cleanup)
        finalize("SimulationApp", lambda: runtime.close_app(code))
        if len(cleanup) != previous_errors:
            finalize(
                "result_receipt_after_cleanup",
                lambda: write_json(args.out / "result.json", result),
            )
        code = 0 if result["status"] == "PASS" else 2
    if pending is not None and not isinstance(pending, Exception):
        raise pending
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", type=Path)
    parser.add_argument("--physics-hz", type=int, choices=[240, 480], default=240)
    parser.add_argument(
        "--gripper-close-rad",
        type=float,
        choices=[0.60, 0.65, 0.70, 0.75],
        default=0.70,
    )
    parser.add_argument("--controller", choices=VERSIONS, default="baseline-v1")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / "stack.log").open("w") as trace:
        faulthandler.dump_traceback_later(120, repeat=True, file=trace)
        try:
            return run_task(args)
        finally:
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    raise SystemExit(main())
