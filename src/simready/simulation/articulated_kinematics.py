"""Forward kinematics over explicit USD joint frames, with bounded serial IK.

This module plans configurations. Collision and dynamic execution are separate
gates; a successful numerical solve is never a successful manipulation task.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def joint_frame(position, quaternion):
    result = np.eye(4)
    result[:3, 3] = position
    result[:3, :3] = Rotation.from_quat(
        [*quaternion.GetImaginary(), quaternion.GetReal()]
    ).as_matrix()
    return result


class UsdJointTree:
    """A rooted acyclic joint graph; rejects ambiguous parents and loops."""

    def __init__(self, stage, root_body):
        from pxr import UsdGeom, UsdPhysics

        if UsdGeom.GetStageMetersPerUnit(stage) != 1:
            raise ValueError("Joint kinematics require meter stage units")
        root = stage.GetPrimAtPath(root_body)
        if not root:
            raise ValueError("Missing root body")
        self.root = str(root_body)
        self.root_pose = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(root)).T
        self.joints = {}
        self.parents = {}
        for prim in stage.Traverse():
            if not prim.IsA(UsdPhysics.Joint):
                continue
            joint = UsdPhysics.Joint(prim)
            a, b = joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()
            if len(a) != 1 or len(b) != 1:
                continue  # World fixture is represented by the explicit root pose.
            parent, child = str(a[0]), str(b[0])
            if child in self.parents:
                raise ValueError(f"Multiple parents/closed loop at {child}")
            kind = prim.GetTypeName()
            if kind not in (
                "PhysicsFixedJoint",
                "PhysicsRevoluteJoint",
                "PhysicsPrismaticJoint",
            ):
                raise ValueError(f"Unsupported joint type: {kind}")
            name = prim.GetName()
            axis = (
                prim.GetAttribute("physics:axis").Get()
                if kind != "PhysicsFixedJoint"
                else "Z"
            )
            item = {
                "name": name,
                "parent": parent,
                "child": child,
                "kind": kind,
                "a": joint_frame(
                    joint.GetLocalPos0Attr().Get(), joint.GetLocalRot0Attr().Get()
                ),
                "b_inv": np.linalg.inv(
                    joint_frame(
                        joint.GetLocalPos1Attr().Get(), joint.GetLocalRot1Attr().Get()
                    )
                ),
                "axis": np.eye(3)[{"X": 0, "Y": 1, "Z": 2}[axis]],
                "path": str(prim.GetPath()),
            }
            if kind != "PhysicsFixedJoint":
                factor = np.pi / 180 if kind == "PhysicsRevoluteJoint" else 1
                item["lower"] = (
                    float(prim.GetAttribute("physics:lowerLimit").Get()) * factor
                )
                item["upper"] = (
                    float(prim.GetAttribute("physics:upperLimit").Get()) * factor
                )
            self.joints[name] = item
            self.parents[child] = item
        self._chains = {}

    def chain(self, body):
        body = str(body)
        if body in self._chains:
            return self._chains[body]
        path, seen, cursor = [], set(), body
        while cursor != self.root:
            if cursor in seen or cursor not in self.parents:
                raise ValueError(f"Body is not in a rooted tree: {body}")
            seen.add(cursor)
            item = self.parents[cursor]
            path.append(item)
            cursor = item["parent"]
        self._chains[body] = list(reversed(path))
        return self._chains[body]

    def pose(self, body, positions):
        pose = self.root_pose.copy()
        for item in self.chain(body):
            motion = np.eye(4)
            value = positions.get(item["name"], 0.0)
            if item["kind"] == "PhysicsRevoluteJoint":
                motion[:3, :3] = Rotation.from_rotvec(item["axis"] * value).as_matrix()
            elif item["kind"] == "PhysicsPrismaticJoint":
                motion[:3, 3] = item["axis"] * value
            pose = pose @ item["a"] @ motion @ item["b_inv"]
        return pose

    def solve(self, body, target, names, seed, fixed=None):
        """Bounded 6D least squares; caller must collision-check the trajectory."""
        target, seed = np.asarray(target, float), np.asarray(seed, float)
        lower = np.array([self.joints[n]["lower"] for n in names])
        upper = np.array([self.joints[n]["upper"] for n in names])
        fixed = dict(fixed or {})

        def errors(q):
            pose = self.pose(body, fixed | dict(zip(names, q, strict=True)))
            angle = Rotation.from_matrix(target[:3, :3] @ pose[:3, :3].T).as_rotvec()
            return np.r_[pose[:3, 3] - target[:3, 3], 0.2 * angle]

        def objective(q):
            return np.r_[errors(q), 0.001 * (q - seed)]

        fit = least_squares(
            objective,
            np.clip(seed, lower + 1e-8, upper - 1e-8),
            bounds=(lower, upper),
            max_nfev=400,
            ftol=1e-10,
            xtol=1e-10,
            gtol=1e-10,
        )
        residual = errors(fit.x)
        if np.linalg.norm(residual[:3]) > 0.001 or np.linalg.norm(residual[3:]) > 0.002:
            raise ValueError(f"IK rejected: residual {residual.tolist()}")
        return fit.x
