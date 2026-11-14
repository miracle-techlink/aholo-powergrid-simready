"""Measured cup arrays and immutable code identity, independent of command logic.

The worker gate below is the historical preliminary gate. Acceptance remains
in evaluation.cup_transfer and is not relaxed by a new controller version.
"""

from pathlib import Path

import numpy as np

from simready.core.artifacts import digest

CODE_FILES = (
    "cup_grasp.py",
    "cup_runtime.py",
    "cup_control.py",
    "cup_recording.py",
    "articulated_kinematics.py",
)


def implementation_hashes():
    root = Path(__file__).parent
    return {name: digest(root / name) for name in CODE_FILES}


class CupRecorder:
    def __init__(self, output, dt):
        self.output, self.dt = Path(output), dt
        self.poses, self.joints, self.commands = [], [], []
        self.forces, self.labels, self.env_forces = [], [], []

    @property
    def frames(self):
        return len(self.labels)

    def append(self, observation, command, label):
        self.poses.append(observation["poses"].copy())
        self.joints.append(observation["joints"].copy())
        self.commands.append(command[0].copy())
        self.forces.append(observation["forces"].copy())
        self.env_forces.append(observation["environment_forces"].copy())
        self.labels.append(label)

    def save(self, result):
        np.savez_compressed(
            self.output / "rollout.npz",
            poses=np.asarray(self.poses),
            joints=np.asarray(self.joints),
            commands=np.asarray(self.commands),
            forces=np.asarray(self.forces),
            environment_forces=np.asarray(self.env_forces),
            phases=np.asarray(self.labels),
            dt=self.dt,
        )
        result["rollout_sha256"] = digest(self.output / "rollout.npz")
        result["frames"] = self.frames
        result["dt_s"] = self.dt

    def finish(self, result, plan, runtime):
        self.save(result)
        poses, forces, labels = self.poses, self.forces, self.labels
        env_forces, dt = self.env_forces, self.dt
        names, bodies = runtime.names, runtime.bodies
        filters, environment_paths = runtime.filters, runtime.environment_paths
        args = runtime.args
        cp = np.asarray(poses)[:, -1, :3]
        f = np.linalg.norm(np.asarray(forces), axis=-1)
        hold = np.asarray(labels) == "HOLD"
        # Initial gate, later independent audit also checks release/penetration.
        checks = {
            "lift_above_10cm": bool(np.min(cp[hold, 2]) > 0.95),
            "bilateral_contact_during_hold": bool(
                np.mean(np.all(f[hold, :2] > 0.05, axis=1)) > 0.95
            ),
            "placed_on_second_table": bool(
                np.linalg.norm(
                    cp[-1] - np.array(plan["destination"]["cup_bottom_xyz_m"])
                )
                < 0.015
            ),
            "released": bool(np.max(f[np.asarray(labels) == "VERIFY", :2]) < 0.01),
            "no_robot_environment_contact": bool(
                np.max(np.linalg.norm(env_forces, axis=-1)) < 1.0
            ),
        }
        result.update(
            status="PASS" if all(checks.values()) else "FAILED",
            checks=checks,
            min_hold_height_m=float(np.min(cp[hold, 2]) - 0.85),
            max_height_m=float(cp[:, 2].max() - 0.85),
            bilateral_hold_fraction=float(np.mean(np.all(f[hold, :2] > 0.05, axis=1))),
            max_force_N=f.max(axis=0).tolist(),
            joint_names=names,
            body_names=bodies + ["Cup"],
            contact_filters=filters,
            environment_filters=environment_paths,
            background_scene=str(args.scene),
            frames=len(poses),
            dt_s=dt,
            real_world_validated=False,
            visual_localization=bool(plan.get("visual_localization")),
            visual_localization_sha256=plan.get("visual_localization_sha256"),
            object_pose_overrides_during_episode=0,
            object_attachment_joint=False,
        )
