"""Read-only deployment checks and exclusive simulation-job ownership."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

from simready.core.artifacts import digest


def inspect_rig(report_path):
    path = Path(report_path).resolve()
    report = json.loads(path.read_text())
    issues = []
    items = [dict(path=report["rig_path"], sha256=report["rig_sha256"])] + report[
        "dependencies"
    ]
    for item in items:
        p = Path(item["path"])
        if not p.is_file() or digest(p) != item["sha256"]:
            issues.append("artifact_missing_or_changed:" + str(p))
    if report.get("units") != "m" or report.get("up_axis") != "Z":
        issues.append("coordinate_contract_invalid")
    if (
        len(report.get("groups", {}).get("legs", [])) != 12
        or len(report.get("groups", {}).get("arm", [])) != 6
    ):
        issues.append("joint_groups_invalid")
    return dict(
        static_ready=not issues,
        issues=issues,
        rig_report_sha256=digest(path),
        task_ready=False,
        task_blockers=[
            "locomotion_controller_not_bound",
            "panel_scene_calibration_pending",
            "collision_checked_arm_trajectory_pending",
            "contact_sensor_binding_pending",
            "ten_trial_evidence_pending",
        ],
        commercial_release_ready=False,
    )


@contextmanager
def simulation_lease(lock_dir="/tmp"):
    """Cooperating jobs use the same exclusive lock; honor all legacy GPU locks.

    Existing locks are never reclaimed automatically, including stale/unknown
    ones. Legacy producers do not share an atomic scheduler; deployment must
    consolidate them before claiming resource isolation.
    """
    root = Path(lock_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "simready_runtime.gpu.lock"
    busy = list(root.glob("*.gpu.lock"))
    if busy:
        raise RuntimeError("SIMULATION_RESOURCE_BUSY: " + ", ".join(map(str, busy)))
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    identity = f"{os.getpid()} simready_runtime\n"
    try:
        os.write(fd, identity.encode())
        os.close(fd)
        fd = None
        other = [p for p in root.glob("*.gpu.lock") if p != lock]
        if other:
            raise RuntimeError(
                "SIMULATION_RESOURCE_BUSY: " + ", ".join(map(str, other))
            )
        yield lock
    finally:
        if fd is not None:
            os.close(fd)
        if lock.is_file() and lock.read_text() == identity:
            lock.unlink()
