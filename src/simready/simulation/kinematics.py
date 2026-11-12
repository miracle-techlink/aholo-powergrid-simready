"""Z1 FK/IK from USD joint frames; collision checking remains a separate gate."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def rigid_transform(value):
    t = np.asarray(value, dtype=float)
    if (
        t.shape != (4, 4)
        or not np.isfinite(t).all()
        or not np.allclose(t[3], [0, 0, 0, 1])
        or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-6)
        or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-6)
    ):
        raise ValueError("Expected rigid transform in meters, without scale/reflection")
    return t


def target_in_arm_frame(world_from_arm, world_from_target):
    return np.linalg.inv(rigid_transform(world_from_arm)) @ rigid_transform(
        world_from_target
    )


def _local(position, quaternion):
    q = quaternion
    t = np.eye(4)
    t[:3, 3] = position
    t[:3, :3] = Rotation.from_quat([*q.GetImaginary(), q.GetReal()]).as_matrix()
    return t


@dataclass
class Chain:
    names: list
    parent_frames: list
    child_frames: list
    axes: list
    lower: np.ndarray
    upper: np.ndarray

    @classmethod
    def from_usd(cls, rig_path):
        from pxr import Usd, UsdGeom, UsdPhysics

        stage = Usd.Stage.Open(str(rig_path))
        if UsdGeom.GetStageMetersPerUnit(stage) != 1:
            raise ValueError("Kinematics require explicitly authored meter units")
        root = "/World/FrankaArm/Physics/"
        names, pf, cf, axes, lo, hi = [], [], [], [], [], []
        previous = "/World/FrankaArm/Geometry/world/link00"
        for i in range(1, 7):
            name = f"joint{i}"
            j = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(root + name))
            if not j or list(map(str, j.GetBody0Rel().GetTargets())) != [previous]:
                raise ValueError("Z1 chain is not connected in expected order")
            body1 = j.GetBody1Rel().GetTargets()
            if len(body1) != 1:
                raise ValueError("Joint must have one child body")
            previous = str(body1[0])
            names.append(name)
            pf.append(_local(j.GetLocalPos0Attr().Get(), j.GetLocalRot0Attr().Get()))
            cf.append(_local(j.GetLocalPos1Attr().Get(), j.GetLocalRot1Attr().Get()))
            axes.append(
                {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[
                    str(j.GetAxisAttr().Get())
                ]
            )
            lo.append(np.deg2rad(j.GetLowerLimitAttr().Get()))
            hi.append(np.deg2rad(j.GetUpperLimitAttr().Get()))
        return cls(names, pf, cf, axes, np.array(lo), np.array(hi))

    def fk(self, q, flange_from_tool=None):
        q = np.asarray(q, float)
        if q.shape != (6,) or not np.isfinite(q).all():
            raise ValueError("Expected six finite joint angles in radians")
        t = np.eye(4)
        for a, b, axis, angle in zip(
            self.parent_frames, self.child_frames, self.axes, q, strict=False
        ):
            r = np.eye(4)
            r[:3, :3] = Rotation.from_rotvec(np.asarray(axis) * angle).as_matrix()
            t = t @ a @ r @ np.linalg.inv(b)
        # Tool transform maps tool coordinates into the flange frame.
        if flange_from_tool is not None:
            t = t @ rigid_transform(flange_from_tool)
        return t

    def solve(self, target, seed, position_tolerance=0.002, rotation_tolerance=0.02):
        target = rigid_transform(target)
        seed = np.asarray(seed, float)
        if (
            seed.shape != (6,)
            or not np.isfinite(seed).all()
            or np.any(seed < self.lower)
            or np.any(seed > self.upper)
        ):
            raise ValueError("IK seed violates joint limits")

        def residual(q):
            pose = self.fk(q)
            dr = Rotation.from_matrix(target[:3, :3] @ pose[:3, :3].T).as_rotvec()
            return np.concatenate([pose[:3, 3] - target[:3, 3], 0.15 * dr])

        fit = least_squares(
            residual,
            seed,
            bounds=(self.lower, self.upper),
            max_nfev=300,
            ftol=1e-10,
            xtol=1e-10,
            gtol=1e-10,
        )
        error = residual(fit.x)
        pos, rot = (
            float(np.linalg.norm(error[:3])),
            float(np.linalg.norm(error[3:]) / 0.15),
        )
        if not fit.success or pos > position_tolerance or rot > rotation_tolerance:
            raise ValueError(
                f"IK unreachable/unconverged: position={pos:.6f}m rotation={rot:.6f}rad"
            )
        return dict(
            joints=dict(zip(self.names, fit.x.tolist(), strict=False)),
            position_error_m=pos,
            orientation_error_rad=rot,
            collision_checked=False,
            execution_authorized=False,
        )
