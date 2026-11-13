"""Check the original patrol against reconstructed static obstacles and G2 shape.

This is a conservative geometry gate, not a closed-loop navigation experiment.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import maximum_filter1d, minimum_filter1d
from scipy.spatial import cKDTree


def inspect_route(rows, centers, resolution, floor, profile):
    tree = cKDTree(centers[:, :2])
    lo = np.asarray([profile[str(i)][0] for i in range(len(profile))])
    hi = np.asarray([profile[str(i)][1] for i in range(len(profile))])
    # Include voxel height and 2 cm geometric margin when selecting body slices.
    half = int(np.ceil((resolution / 2 + 0.02) / 0.025))
    lo = minimum_filter1d(lo, size=half * 2 + 1, axis=0)
    hi = maximum_filter1d(hi, size=half * 2 + 1, axis=0)
    metrics = []
    for row in rows:
        pos = np.asarray(row["robot_position"])
        q = centers[tree.query_ball_point(pos[:2], 0.85)] - pos
        yaw = np.deg2rad(row["yaw"])
        rot = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
        local = q[:, :2] @ rot
        bins = np.floor(q[:, 2] / 0.025).astype(int)
        valid = (bins >= -half) & (bins < len(lo) + half)
        bins = np.clip(bins, 0, len(lo) - 1)
        pad = resolution / 2 * (abs(np.cos(yaw)) + abs(np.sin(yaw))) + 0.02
        overlap = valid & ((local >= lo[bins] - pad) & (local <= hi[bins] + pad)).all(
            axis=1
        )
        bottom = pos[2] - np.r_[pos[:2], 1] @ floor
        metrics.append(
            {
                "frame": row["frame"],
                "overlapping_obstacle_cells": int(overlap.sum()),
                "root_floor_clearance_m": float(bottom),
            }
        )
    return metrics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--trajectory", type=Path, required=True)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--x-range",
        nargs=2,
        type=float,
        help="Explicit shorter patrol span in source coordinates; preserves duration and turn timing",
    )
    ap.add_argument("--follow-camera", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    data = np.load(args.assets / "scene/obstacles.npz", allow_pickle=False)
    centers = (np.argwhere(data["occupied"]) + 0.5) * data["resolution"] + data[
        "origin"
    ]
    transform = data["old_to_world"]
    centers = centers @ transform[:3, :3].T + transform[:3, 3]
    profile = json.loads(args.profile.read_text())
    original = json.loads(args.trajectory.read_text())
    if args.x_range:
        if not np.isfinite(args.x_range).all() or args.x_range[1] <= args.x_range[0]:
            ap.error("--x-range must be finite and increasing")
        low, high = (
            min(r["robot_position"][0] for r in original),
            max(r["robot_position"][0] for r in original),
        )
        if high <= low:
            ap.error("Source patrol has no longitudinal span")
        for row in original:
            row["robot_position"][0] = float(
                np.interp(row["robot_position"][0], [low, high], args.x_range)
            )
    trials, selected = [], None
    for y in [-1.6, -1.65, -1.7, -1.75, -1.8, -1.55, -1.5]:
        rows = json.loads(json.dumps(original))
        for row in rows:
            row["robot_position"][1] = y
            row["robot_position"][2] = float(
                np.r_[row["robot_position"][:2], 1] @ data["floor"] + 0.01
            )
            row["robot_position"] = (
                np.asarray(row["robot_position"]) @ transform[:3, :3].T
                + transform[:3, 3]
            ).tolist()
            for key in ["eye", "target"]:
                row[key] = (
                    np.asarray(row[key]) @ transform[:3, :3].T + transform[:3, 3]
                ).tolist()
        metrics = inspect_route(
            rows, centers, float(data["resolution"]), np.zeros(3), profile
        )
        maximum = max(m["overlapping_obstacle_cells"] for m in metrics)
        trials.append(
            {
                "y": y,
                "max_overlap": maximum,
                "blocked_frames": sum(
                    m["overlapping_obstacle_cells"] > 0 for m in metrics
                ),
            }
        )
        if maximum == 0:
            selected = rows, metrics
            break
    if selected is None:
        # Search a smooth centerline inside the same captured aisle. This plans
        # geometry only; it does not turn authored motion into policy control.
        xs = np.unique(
            np.r_[
                np.linspace(-1.3, 3.5, 193), [r["robot_position"][0] for r in original]
            ]
        )
        ys = np.linspace(-2.0, -1.1, 91)
        grid_rows = []
        for x in xs:
            for y in ys:
                old = np.array([x, y, np.array([x, y, 1]) @ data["floor"] + 0.01])
                pos = old @ transform[:3, :3].T + transform[:3, 3]
                for yaw in [0, 180]:
                    grid_rows.append(
                        {
                            "frame": len(grid_rows),
                            "robot_position": pos.tolist(),
                            "yaw": yaw,
                        }
                    )
        grid_metrics = inspect_route(
            grid_rows, centers, float(data["resolution"]), np.zeros(3), profile
        )
        blocked = (
            np.asarray([m["overlapping_obstacle_cells"] > 0 for m in grid_metrics])
            .reshape(len(xs), len(ys), 2)
            .any(axis=2)
        )
        cost = np.full(blocked.shape, np.inf)
        back = np.full(blocked.shape, -1, dtype=int)
        cost[0, ~blocked[0]] = (ys[~blocked[0]] + 1.6) ** 2
        for i in range(1, len(xs)):
            for j in np.flatnonzero(~blocked[i]):
                transition = cost[i - 1] + 50 * (ys - ys[j]) ** 2
                transition[abs(ys - ys[j]) > max(0.011, 1.2 * (xs[i] - xs[i - 1]))] = (
                    np.inf
                )
                k = int(np.argmin(transition))
                cost[i, j] = transition[k] + (ys[j] + 1.6) ** 2
                back[i, j] = k
        if np.isfinite(cost[-1]).any():
            j = int(np.argmin(cost[-1]))
            lane = [j]
            for i in range(len(xs) - 1, 0, -1):
                j = back[i, j]
                lane.append(j)
            centerline = PchipInterpolator(xs, ys[lane[::-1]])
            rows = json.loads(json.dumps(original))
            for row in rows:
                x = row["robot_position"][0]
                y = float(centerline(x))
                old = np.array([x, y, np.array([x, y, 1]) @ data["floor"] + 0.01])
                row["robot_position"] = (
                    old @ transform[:3, :3].T + transform[:3, 3]
                ).tolist()
                for key in ["eye", "target"]:
                    row[key] = (
                        np.asarray(row[key]) @ transform[:3, :3].T + transform[:3, 3]
                    ).tolist()
            metrics = inspect_route(
                rows, centers, float(data["resolution"]), np.zeros(3), profile
            )
            maximum = max(m["overlapping_obstacle_cells"] for m in metrics)
            trials.append(
                {
                    "method": "aisle centerline dynamic programming + shape-preserving interpolation",
                    "max_overlap": maximum,
                    "blocked_frames": sum(
                        m["overlapping_obstacle_cells"] > 0 for m in metrics
                    ),
                    "max_lateral_change_from_original_m": float(
                        max(abs(ys[lane] + 1.6))
                    ),
                    "centerline_old": np.c_[xs, ys[lane[::-1]]].tolist(),
                }
            )
            if maximum == 0:
                selected = rows, metrics
    report = {
        "status": "PASS" if selected else "FAILED",
        "scope": "900-frame conservative G2 shape vs occupied-cell clearance; authored motion",
        "robot_navigation_validated": False,
        "real_world_validated": False,
        "voxel_resolution_m": float(data["resolution"]),
        "added_clearance_margin_m": 0.02,
        "trials": trials,
        "source_trajectory_sha256": hashlib.sha256(
            args.trajectory.read_bytes()
        ).hexdigest(),
        "robot_profile_sha256": hashlib.sha256(args.profile.read_bytes()).hexdigest(),
        "scene_obstacles_sha256": hashlib.sha256(
            (args.assets / "scene/obstacles.npz").read_bytes()
        ).hexdigest(),
    }
    if selected:
        if args.follow_camera:
            for row in selected[0]:
                pos = np.asarray(row["robot_position"])
                theta = row["time_s"] / 30 * 2 * np.pi
                row["eye"] = (pos + [0.45 + 0.20 * np.sin(theta), -1.5, 2.70]).tolist()
                row["target"] = (pos + [0, 0, 1.0]).tolist()
        report["source_x_range_override"] = args.x_range
        report["camera_mode"] = (
            "elevated follow with gentle lateral orbit"
            if args.follow_camera
            else "original fixed camera"
        )
        (args.out / "trajectory.json").write_text(
            json.dumps(selected[0], indent=2) + "\n"
        )
        report["frames"] = selected[1]
        report["checked_trajectory_sha256"] = hashlib.sha256(
            (args.out / "trajectory.json").read_bytes()
        ).hexdigest()
    (args.out / "route_check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "frames"}), flush=True)
    return 0 if selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
