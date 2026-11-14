"""Versioned cup commands, separate from the native runtime and independent audit.

baseline-v1 preserves the prior phase schedule and placement correction.
stabilized-v2 is an opt-in, unvalidated feedback candidate using privileged
simulator poses. Its command bounds are not measured contact-force guarantees.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass

import numpy as np
from scipy.spatial.transform import Rotation

VERSIONS = ("baseline-v1", "stabilized-v2")
GRIPPER = {
    "idx71_gripper_r_inner_joint1": -1,
    "idx72_gripper_r_outer_joint1": -1,
    "idx73_gripper_r_left_support_joint": 1,
    "idx74_gripper_r_inner_joint2": 1,
    "idx75_gripper_r_outer_joint2": 1,
    "idx76_gripper_r_right_support_joint": 1,
}


@dataclass(frozen=True)
class StabilizationConfig:
    feedback_hz: int = 20
    tilt_deadband_deg: float = 2.0
    correction_rate_deg_s: float = 8.0
    correction_limit_deg: float = 20.0
    arm_offset_rate_rad_s: float = 0.3
    slip_trigger_m: float = 0.003
    extra_close_rate_rad_s: float = 0.02
    maximum_close_rad: float = 0.75
    inhibit_tightening_above_n: float = 20.0
    release_tilt_limit_deg: float = 4.0
    release_support_weight_fraction: float = 0.6
    release_confirmation_s: float = 0.15


def controller_spec(version):
    if version not in VERSIONS:
        raise ValueError(f"Unknown cup controller: {version}")
    return {
        "version": version,
        "feedback": "privileged simulator rigid-body poses and contact forces",
        "candidate_physically_validated": False if version == "stabilized-v2" else None,
        "configuration": asdict(StabilizationConfig())
        if version == "stabilized-v2"
        else {},
    }


def phase_schedule(closed):
    return [
        ("SETTLE", 1.0, "pregrasp", "pregrasp", 0, 0),
        ("APPROACH", 2.0, "pregrasp", "grasp", 0, 0),
        ("CLOSE", 1.5, "grasp", "grasp", 0, closed),
        ("LIFT", 2.0, "grasp", "lift", closed, closed),
        ("HOLD", 2.0, "lift", "lift", closed, closed),
        ("TRANSFER", 4.0, "lift", "transfer", closed, closed),
        ("PLACE", 4.0, "transfer", "place", closed, closed),
        ("RELEASE", 2.0, "place", "place", closed, 0),
        ("RETREAT", 2.0, "place", "transfer", 0, 0),
        ("VERIFY", 2.0, "transfer", "transfer", 0, 0),
    ]


def quintic(t):
    return t * t * t * (10 - 15 * t + 6 * t * t)


def cup_tilt(pose):
    upright = Rotation.from_quat(pose[3:]).as_matrix()[:, 2]
    return float(np.degrees(np.arccos(np.clip(upright[2], -1, 1))))


class CupController:
    def __init__(
        self,
        plan,
        names,
        *,
        physics_hz=240,
        closed=0.70,
        version="baseline-v1",
        mass_kg=None,
    ):
        self.spec = controller_spec(version)
        if physics_hz not in (240, 480) or closed not in (0.60, 0.65, 0.70, 0.75):
            raise ValueError("Unsupported cup sampling clock or closing command")
        self.plan = copy.deepcopy(plan)
        self.names = list(names)
        self.arm_i = [names.index(name) for name in plan["arm_names"]]
        self.grip_i = {names.index(name): sign for name, sign in GRIPPER.items()}
        self.q = np.array(
            [[plan["initial"].get(name, 0.0) for name in names]], dtype=np.float32
        )
        self.waypoints = {row["name"]: np.array(row["q"]) for row in plan["waypoints"]}
        self.dt, self.closed = 1 / physics_hz, closed
        self.phases = phase_schedule(closed)
        self.config = StabilizationConfig()
        self.stride = physics_hz // self.config.feedback_hz
        self.mass_kg = mass_kg
        if version == "stabilized-v2" and (
            mass_kg is None or not np.isfinite(mass_kg) or mass_kg <= 0
        ):
            raise ValueError("Feedback requires the measured positive cup mass")
        self.extra_close = 0.0
        self.correction = Rotation.identity()
        self.offset = np.zeros(len(self.arm_i))
        self.target_offset = self.offset.copy()
        self.reference_relative_position = None
        self.support_seconds = 0.0
        self.events = []
        self.placement_feedback = None

    def begin_phase(self, phase, observation, kinematics):
        self.label, duration, qa, qb, self.ga, self.gb = phase
        self.steps = round(duration / self.dt)
        if self.label == "PLACE":
            palm, held = observation["palm"], observation["poses"][-1]
            destination = np.array(self.plan["destination"]["cup_bottom_xyz_m"])
            end = palm[:3] + destination - held[:3]
            target = np.eye(4)
            target[:3, :3] = Rotation.from_quat(palm[3:]).as_matrix()
            seed = observation["joints"][self.arm_i].copy()
            samples = []
            for fraction in np.linspace(0, 1, 81):
                target[:3, 3] = palm[:3] * (1 - fraction) + end * fraction
                seed = kinematics.solve(target, seed)
                samples.append(seed.tolist())
            self.plan["paths"]["transfer_place"] = samples
            self.plan["paths"]["place_transfer"] = list(reversed(samples))
            self.waypoints["place"] = np.array(samples[-1])
            self.placement_feedback = {
                "source": "measured simulator rigid-body poses; privileged canary feedback",
                "held_cup_xyz_m": held[:3].tolist(),
                "gripper_destination_xyz_m": end.tolist(),
                "release_clearance_m": 0.0,
            }
            # This newly measured path already contains the preceding wrist
            # correction. Applying that correction twice would change the grasp.
            self.correction = Rotation.identity()
            self.offset[:] = self.target_offset[:] = 0
        self.path = np.array(
            self.plan["paths"].get(
                qa + "_" + qb,
                [self.waypoints[qa].tolist(), self.waypoints[qb].tolist()],
            )
        )
        if self.label == "LIFT":
            self.reference_relative_position = self._relative_cup(observation)
        if self.label == "RELEASE" and self.spec["version"] == "stabilized-v2":
            if self.support_seconds + 1e-9 < self.config.release_confirmation_s:
                self.events.append(
                    {
                        "phase": self.label,
                        "event": "RELEASE_BLOCKED",
                        "support_seconds": self.support_seconds,
                    }
                )
                raise ValueError(
                    "Controller refused release: upright destination support not confirmed"
                )

    @staticmethod
    def _relative_cup(observation):
        palm, cup = observation["palm"], observation["poses"][-1]
        return Rotation.from_quat(palm[3:]).inv().apply(cup[:3] - palm[:3])

    def command(self, step, observation, kinematics):
        if not 0 <= step < self.steps:
            raise ValueError("Command step outside phase")
        u = quintic((step + 1) / self.steps)
        self.q[0, self.arm_i] = [
            np.interp(u, np.linspace(0, 1, len(self.path)), self.path[:, j])
            for j in range(len(self.arm_i))
        ]
        close = self.ga * (1 - u) + self.gb * u
        if self.spec["version"] == "stabilized-v2":
            close = self._stabilize(step, u, close, observation, kinematics)
        for index, sign in self.grip_i.items():
            self.q[0, index] = sign * close
        return self.q.copy()

    def _stabilize(self, step, u, close, observation, kinematics):
        config = self.config
        active = self.label in {"LIFT", "HOLD", "TRANSFER", "PLACE"}
        if active and step % self.stride == 0:
            interval = self.stride * self.dt
            cup, palm = observation["poses"][-1], observation["palm"]
            tilt = cup_tilt(cup)
            slip = float(
                np.linalg.norm(
                    self._relative_cup(observation) - self.reference_relative_position
                )
            )
            forces = np.linalg.norm(observation["forces"], axis=-1)
            if (slip > config.slip_trigger_m or tilt > config.tilt_deadband_deg) and (
                np.max(forces[:2]) < config.inhibit_tightening_above_n
            ):
                self.extra_close = min(
                    config.maximum_close_rad - self.closed,
                    self.extra_close + config.extra_close_rate_rad_s * interval,
                )
            if tilt > config.tilt_deadband_deg:
                up = Rotation.from_quat(cup[3:]).as_matrix()[:, 2]
                axis = np.cross(up, [0, 0, 1])
                if np.linalg.norm(axis) > 1e-9:
                    delta = min(
                        tilt - config.tilt_deadband_deg,
                        config.correction_rate_deg_s * interval,
                    )
                    self.correction = (
                        Rotation.from_rotvec(
                            axis / np.linalg.norm(axis) * np.radians(delta)
                        )
                        * self.correction
                    )
                    angle = self.correction.magnitude()
                    if angle > np.radians(config.correction_limit_deg):
                        self.correction = Rotation.from_rotvec(
                            self.correction.as_rotvec()
                            * np.radians(config.correction_limit_deg)
                            / angle
                        )
            nominal = kinematics.palm_pose(self.q[0])
            target = nominal.copy()
            correction = self.correction.as_matrix()
            target[:3, :3] = correction @ nominal[:3, :3]
            target[:3, 3] += (correction - np.eye(3)) @ (palm[:3] - cup[:3])
            solved = kinematics.solve(target, observation["joints"][self.arm_i])
            self.target_offset = solved - self.q[0, self.arm_i]
            self.events.append(
                {
                    "phase": self.label,
                    "step": step,
                    "tilt_deg": tilt,
                    "relative_slip_m": slip,
                    "close_rad": self.closed + self.extra_close,
                    "correction_deg": float(np.degrees(self.correction.magnitude())),
                }
            )
        if self.label == "PLACE":
            support = float(observation["forces"][3, 2])
            if (
                cup_tilt(observation["poses"][-1]) < config.release_tilt_limit_deg
                and support
                > self.mass_kg * 9.81 * config.release_support_weight_fraction
            ):
                self.support_seconds += self.dt
            else:
                self.support_seconds = 0.0
        if active or self.label == "RELEASE":
            self.offset += np.clip(
                self.target_offset - self.offset,
                -config.arm_offset_rate_rad_s * self.dt,
                config.arm_offset_rate_rad_s * self.dt,
            )
            self.q[0, self.arm_i] += self.offset
            close += self.extra_close * (1 - u if self.label == "RELEASE" else 1)
        elif self.label == "RETREAT":
            self.q[0, self.arm_i] += self.offset * (1 - u)
        return close
