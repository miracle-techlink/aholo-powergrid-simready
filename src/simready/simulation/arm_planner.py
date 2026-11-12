"""Conservative USD collision-box checks for sampled Z1 joint paths.

Boxes enclose the authored collision shapes. This is a local simulated-scene
planner, not a certified continuous collision or full-body stability checker.
"""

import itertools

import numpy as np
from scipy.spatial.transform import Rotation


def corners(lo, hi):
    return np.array(list(itertools.product(*zip(lo, hi, strict=False))))


def transformed_bounds(points, transform):
    points = points @ transform[:3, :3].T + transform[:3, 3]
    return points.min(axis=0), points.max(axis=0)


def advance_joint_target(current, waypoint, dt, max_speed=0.5):
    """One shared fraction keeps a command on the checked joint segment."""
    current = np.asarray(current, float)
    waypoint = np.asarray(waypoint, float)
    if dt <= 0 or max_speed <= 0 or not np.isfinite([current, waypoint]).all():
        raise ValueError("Finite joint targets and positive timing required")
    delta = waypoint - current
    fraction = min(1.0, max_speed * dt / max(float(np.max(abs(delta))), 1e-12))
    return current + fraction * delta


class ArmScenePlanner:
    def __init__(self, stage, chain, target_cap, clearance=0.001):
        from pxr import Usd, UsdGeom, UsdPhysics

        self.chain, self.target_cap, self.clearance = chain, target_cap, clearance
        self.compact_home = np.array([0.0, 0.5, -0.9, 0.0, 0.3, 0.0])
        # link05 has no collision shape. The authored link04 wrist housing and
        # link06 flange housing overlap at that joint, including at home.
        # The flange-mounted shaft begins inside this same assembly. Keep the
        # exposed fingertip subject to checks; this is a declared assembly pair.
        self.assembly_exclusion = ("link04", "link06", ("cylinder", "TaskPressShaft"))
        self.shapes = []
        self.environment = []
        cache = UsdGeom.BBoxCache(
            0, ["default", "render", "proxy", "guide"], False, True
        )
        for p in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
            if not p.HasAPI(UsdPhysics.CollisionAPI):
                continue
            if UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is False:
                continue
            path = str(p.GetPath())
            if path.startswith("/World/PanelEnvironment/"):
                box = cache.ComputeWorldBound(p).ComputeAlignedRange()
                lo, hi = np.array(box.GetMin()), np.array(box.GetMax())
                if not np.isfinite([lo, hi]).all() or (hi <= lo).any():
                    raise ValueError("Invalid environment collision bounds: " + path)
                self.environment.append((path, lo, hi))
            elif path.startswith(("/World/Lite3/", "/World/FrankaArm/")):
                owner = p
                while owner and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                    owner = owner.GetParent()
                if not owner:
                    continue
                box = cache.ComputeRelativeBound(p, owner).ComputeAlignedRange()
                lo, hi = np.array(box.GetMin()), np.array(box.GetMax())
                owner_name = owner.GetName()
                physical_owner = owner_name
                if (
                    owner.GetAttribute("simready:toolKind").Get()
                    == "passive_axial_spring_plunger"
                ):
                    # Plan against the whole passive travel envelope. Never
                    # treat a spring-mounted tip as a fixed world obstacle.
                    flange = owner.GetRelationship("simready:flangeBody").GetTargets()[
                        0
                    ]
                    owner_name = stage.GetPrimAtPath(flange).GetName()
                    offset = np.array(
                        owner.GetAttribute("simready:nominalOffset").Get()
                    )
                    lo += offset
                    hi += offset
                    lo[0] -= float(owner.GetAttribute("simready:travel_m").Get())
                self.shapes.append(
                    dict(
                        path=path,
                        body=owner_name,
                        physical_owner=physical_owner,
                        points=corners(lo, hi),
                    )
                )
        self.env_lo = np.array([x[1] for x in self.environment])
        self.env_hi = np.array([x[2] for x in self.environment])

    def link_poses(self, q, world_from_base):
        result = {"link00": world_from_base}
        pose = world_from_base.copy()
        for i, (parent, child, axis, angle) in enumerate(
            zip(
                self.chain.parent_frames,
                self.chain.child_frames,
                self.chain.axes,
                q,
                strict=False,
            ),
            1,
        ):
            turn = np.eye(4)
            turn[:3, :3] = Rotation.from_rotvec(np.asarray(axis) * angle).as_matrix()
            pose = pose @ parent @ turn @ np.linalg.inv(child)
            result[f"link0{i}"] = pose.copy()
        return result

    def check(self, q, world_from_base, fixed_body_poses, allow_tip_contact=False):
        q = np.asarray(q)
        if np.any(q < self.chain.lower) or np.any(q > self.chain.upper):
            return ["joint_limit"]
        poses = self.link_poses(q, world_from_base)
        local_poses = self.link_poses(q, np.eye(4))
        base_from_world = np.linalg.inv(world_from_base)
        moving, fixed, local_bounds = [], [], {}
        for shape in self.shapes:
            name = shape["body"]
            if name in poses:
                lo, hi = transformed_bounds(shape["points"], poses[name])
                moving.append((shape, lo, hi))
                local_bounds[shape["path"]] = transformed_bounds(
                    shape["points"], local_poses[name]
                )
            elif name in fixed_body_poses:
                lo, hi = transformed_bounds(
                    shape["points"], base_from_world @ fixed_body_poses[name]
                )
                fixed.append((shape, lo, hi))
        collisions = []
        for shape, lo, hi in moving:
            overlap = (np.minimum(hi, self.env_hi) - np.maximum(lo, self.env_lo)).min(
                axis=1
            )
            for i in np.where(overlap > -self.clearance)[0]:
                target = self.environment[i][0]
                if (
                    allow_tip_contact
                    and shape["path"].endswith(("/TaskPressTip", "/TaskPressShaft"))
                    and target.startswith(self.target_cap + "/")
                ):
                    continue
                collisions.append([shape["path"], target])
            # The mount is intentionally adjacent to torso and arm base.
            if shape["body"] in ["link00", "link01"]:
                continue
            lo, hi = local_bounds[shape["path"]]
            for other, low, high in fixed:
                if (np.minimum(hi, high) - np.maximum(lo, low)).min() > -self.clearance:
                    collisions.append([shape["path"], other["path"]])
        # Adjacent joint housings overlap by design; check nonadjacent links.
        for i, (shape, lo, hi) in enumerate(moving):
            lo, hi = local_bounds[shape["path"]]
            for other, low, high in moving[i + 1 :]:
                if abs(int(shape["body"][-1]) - int(other["body"][-1])) <= 1:
                    continue
                pair = {shape["body"], other["body"]}
                distal = shape if shape["body"] == "link06" else other
                if (
                    pair == {"link04", "link06"}
                    and distal["path"].rsplit("/", 1)[-1] in self.assembly_exclusion[2]
                ):
                    continue
                low, high = local_bounds[other["path"]]
                if (np.minimum(hi, high) - np.maximum(lo, low)).min() > -self.clearance:
                    collisions.append([shape["path"], other["path"]])
        return collisions

    def check_segment(
        self, start, end, world_from_base, fixed_body_poses, allow_tip_contact=False
    ):
        count = max(2, int(np.ceil(np.max(abs(np.array(end) - start)) / 0.02)) + 1)
        for u in np.linspace(0, 1, count):
            q = np.asarray(start) * (1 - u) + np.asarray(end) * u
            collisions = self.check(
                q, world_from_base, fixed_body_poses, allow_tip_contact
            )
            if collisions:
                return dict(
                    passed=False,
                    fraction=float(u),
                    collisions=collisions[:8],
                    samples=count,
                )
        return dict(passed=True, samples=count)

    def plan(self, start, end, world_from_base, fixed_body_poses):
        start, end = np.asarray(start), np.asarray(end)
        turn = start.copy()
        turn[0] = end[0]
        compact_start = self.compact_home.copy()
        compact_start[0] = start[0]
        compact_end = self.compact_home.copy()
        compact_end[0] = end[0]
        # Fold before yawing, keeping the carried arm mass close to the torso.
        candidates = [
            [start, compact_start, compact_end, end],
            [start, end],
            [start, turn, end],
        ]
        rejected = []
        for index, points in enumerate(candidates):
            checks = [
                self.check_segment(a, b, world_from_base, fixed_body_poses)
                for a, b in zip(points, points[1:], strict=False)
            ]
            if all(c["passed"] for c in checks):
                return dict(
                    passed=True,
                    method="sampled joint waypoints with conservative collision boxes",
                    candidate=index,
                    waypoints=[q.tolist() for q in points],
                    checks=checks,
                    max_sample_delta_rad=0.02,
                    clearance_m=self.clearance,
                    self_check_frame="arm base; independent of world yaw/tilt",
                    declared_assembly_exclusion=self.assembly_exclusion,
                    continuous_collision_certified=False,
                )
            rejected.append(checks)
        raise ValueError("No checked arm path: " + str(rejected))
