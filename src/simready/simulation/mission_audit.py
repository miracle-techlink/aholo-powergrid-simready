#!/usr/bin/env python3
"""Independently replay contact events and audit measured task evidence."""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from simready.core.artifacts import digest, write_json
from simready.simulation.button import ButtonSample
from simready.simulation.kinematics import Chain
from simready.simulation.panel_controls import PanelControl
from simready.simulation.panel_perception import detect_stop_candidate, localize_rgbd

MODULE_DIR = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    a = ap.parse_args()
    p = a.run.resolve()
    result = json.loads((p / "result.json").read_text())
    meta = json.loads((p / "metadata.json").read_text())
    rows = [json.loads(x) for x in (p / "sensors.jsonl").read_text().splitlines()]
    cfg = meta["config"]
    manifest = json.loads((Path(cfg["environment_manifest"])).read_text())
    spec = dict(
        next(x for x in manifest["buttons"] if x["id"] == cfg["audit_target_id"])
    )
    for key in ["cap_prim", "base_prim", "joint_prim"]:
        spec[key] = spec[key].replace("/World/", "/World/PanelEnvironment/", 1)
    adapter = PanelControl(
        spec,
        meta["target_contact_pair"][1],
        lambda _: (_ for _ in ()).throw(AssertionError("Unexpected cap command")),
    )
    events = []
    state = "RUNNING"
    load_matches = True
    for row in rows:
        v = adapter.observe(
            ButtonSample(
                row["step"],
                row["t"],
                spec["cap_prim"],
                meta["target_contact_pair"][1],
                row["target_depth_m"],
                row["target_contact_N"],
            )
        )
        if v["event"]:
            events.append(v["event"])
            state = "STOPPED"
        load_matches = load_matches and row["load_state"] == state
    perception = []
    for recorded in json.loads((p / "perception.json").read_text()):
        d = np.load(p / (recorded["label"] + "_rgbd.npz"))
        det = detect_stop_candidate(d["rgb"])
        assert det["found"] == recorded["detection"]["found"]
        if det["found"]:
            position = localize_rgbd(
                det, d["depth"], d["world_from_camera"], float(d["focal_px"])
            )["surface_world_m"]
            assert np.allclose(
                position, recorded["localization"]["surface_world_m"], atol=1e-8
            )
            # Independent truth comparison for audit only, never sent to IK.
            true_surface = (
                np.array(spec["rest_center_world"])
                - np.array(spec["inward_axis_world"]) * spec["cap_height_m"] / 2
            )
            perception.append(
                dict(
                    label=recorded["label"],
                    error_to_collision_front_m=float(
                        np.linalg.norm(np.array(position) - true_surface)
                    ),
                )
            )
    replay = np.load(p / "measured_replay.npz")
    contract_path = Path(cfg["rig_report"])
    contract = json.loads(contract_path.read_text())
    chain = Chain.from_usd(contract_path.parent / contract["rig_path"])
    names = meta["joint_names"]
    ai = [names.index(n) for n in chain.names]
    base_i = next(
        i for i, x in enumerate(replay["body_paths"]) if str(x).endswith("/link00")
    )
    tool_i = next(
        i for i, x in enumerate(replay["body_paths"]) if str(x).endswith("/link06")
    )
    max_fk_error = 0.0
    max_tip_error = 0.0
    for i, step in enumerate(replay["steps"]):
        b = replay["body_poses"][i, base_i]
        actual = replay["body_poses"][i, tool_i]
        row = rows[int(step)]
        frame = np.eye(4)
        frame[:3, 3] = b[:3]
        frame[:3, :3] = Rotation.from_quat(b[3:]).as_matrix()
        predicted = frame @ chain.fk(np.array(row["q"])[ai])
        max_fk_error = max(
            max_fk_error, float(np.linalg.norm(predicted[:3, 3] - actual[:3]))
        )
        tip_offset = np.array(cfg["tool"]["flange_offset_m"], float)
        if meta.get("press_tool"):
            tip_offset[0] += row["q"][names.index(meta["press_tool"]["joint_name"])]
        tip = actual[:3] + Rotation.from_quat(actual[3:]).apply(tip_offset)
        max_tip_error = max(
            max_tip_error, float(np.linalg.norm(tip - row["tip_world"]))
        )
    plans = json.loads((p / "motion_plans.json").read_text())
    phases = [x["phase"] for x in result["transitions"]]
    qd = np.array([r["qd"] for r in rows])
    rpy = np.array([r["rpy"] for r in rows])
    root = np.array([r["root"] for r in rows])
    max_other = max(r["unwanted_environment_contact_N"] for r in rows)
    checks = dict(
        reported_pass=result["passed"],
        one_actual_press=len(events) == 1,
        events_reproduce=events == result["press_events"],
        load_changes_only_from_contact=load_matches and state == "STOPPED",
        physical_displacement_gt_1m=float(np.linalg.norm(root[-1, :2] - root[0, :2]))
        > 1.0,
        unique_near_image_verified=any(
            x["label"].startswith("dock") and x["error_to_collision_front_m"] < 0.003
            for x in perception
        ),
        phase_order=phases
        == ["NAVIGATE", "SETTLE", "REACH", "PRESS", "WITHDRAW", "RETRACT", "SUCCEEDED"],
        positive_ground_sensor=result["ground_contact_positive_control"],
        no_unintended_contact=max_other < 1.0,
        force_below_limit=max(r["target_contact_N"] for r in rows)
        < cfg["contact"]["max_force_N"],
        cap_released=rows[-1]["target_depth_m"] < spec["release_depth_m"],
        arm_retracted=np.max(
            abs(np.array(rows[-1]["q"])[ai] - np.array(rows[0]["q"])[ai])
        )
        < 0.03,
        pose_replay_matches_physics=max_fk_error < 1e-5 and max_tip_error < 1e-6,
        plans_pass=all(x["passed"] for x in plans)
        and {x["phase"] for x in plans} == {"REACH", "PRESS", "RETRACT"},
        bounded_arm_speed=float(abs(qd[:, ai]).max()) < 1.0,
        no_fall=float(abs(rpy[:, :2]).max()) < 0.35,
        no_root_or_button_pose_writes=meta["root_writes_after_initialization"]
        == meta["button_pose_writes"]
        == 0,
    )
    checks = {k: bool(v) for k, v in checks.items()}
    spring_summary = None
    if meta.get("press_tool"):
        compression = np.array([r["tool_compression_m"] for r in rows])
        travel = meta["press_tool"]["travel_m"]
        contact_rows = [r for r in rows if r["target_contact_N"] > 0.3]
        spring_summary = dict(
            max_compression_m=float(compression.max()),
            final_compression_m=float(compression[-1]),
            spring_N_m=meta["press_tool"]["spring_N_m"],
            travel_m=travel,
            contact_samples=len(contact_rows),
        )
        checks.update(
            passive_spring_compressed=bool(compression.max() > 0.0005),
            spring_not_bottomed=bool(compression.max() < travel * 0.98),
            spring_released=bool(abs(compression[-1]) < 0.00015),
        )
    report = dict(
        passed=all(checks.values()),
        checks=checks,
        measured_press_events=events,
        perception=perception,
        duration_s=rows[-1]["t"],
        displacement_m=float(np.linalg.norm(root[-1, :2] - root[0, :2])),
        max_arm_speed_rad_s=float(abs(qd[:, ai]).max()),
        max_tilt_deg=float(np.rad2deg(abs(rpy[:, :2]).max())),
        max_fk_error_m=max_fk_error,
        max_tip_replay_error_m=max_tip_error,
        max_other_contact_N=max_other,
        spring_tool=spring_summary,
        result_sha256=digest(p / "result.json"),
        sensor_sha256=digest(p / "sensors.jsonl"),
        replay_sha256=digest(p / "measured_replay.npz"),
        audit_code_sha256=digest(__file__),
        electrical_function_verified=False,
        whole_scene_certified=False,
    )
    write_json(p / "audit.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
