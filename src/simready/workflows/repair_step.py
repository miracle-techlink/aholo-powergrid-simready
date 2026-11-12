"""Execute and verify one repair proposal; the outer loop owns promotion and memory."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from simready.core.evidence import ensure_unchanged, reference
from simready.core.types import JsonObject
from simready.evaluation.regression import compare_scorecards
from simready.evaluation.verification import verify_candidate
from simready.execution import run_task_graph
from simready.repair import RepairPluginRegistry


@dataclass
class RepairTrialResult:
    """Trial evidence and measurements, without committing a scene or a skill."""

    evidence: dict[str, JsonObject] = field(default_factory=dict)
    comparison: JsonObject | None = None
    candidate: Path | None = None
    scorecard: JsonObject | None = None
    defects: JsonObject | None = None
    observations: JsonObject | None = None
    error: str | None = None


def run_repair_trial(
    proposal: JsonObject,
    *,
    registry: RepairPluginRegistry,
    config: JsonObject,
    paths: dict[str, Path],
    current: Path,
    before: JsonObject,
    observations: JsonObject,
    protocol_hash: str,
    round_dir: Path,
    cache_dir: Path,
    remaining: Callable[[float], float],
    frozen: list[JsonObject],
    inventory: JsonObject,
) -> RepairTrialResult:
    """Run a trusted task plan and independently compare its candidate to the baseline."""
    result = RepairTrialResult()
    budget = config["budget"]
    try:
        plan, final_task = registry.build_task_plan(
            proposal,
            scene_id=config["scene_id"],
            candidate=current,
            protocol=paths["protocol"],
            directory=round_dir / "bindings",
            input_files=observations["input_files"],
        )
        for task in plan["tasks"]:
            task["timeout_s"] = min(
                task["timeout_s"], remaining(budget["wall_seconds"])
            )
            task["queue_timeout_s"] = min(
                task.get("queue_timeout_s", 300), remaining(budget["wall_seconds"])
            )
        execution = run_task_graph(
            plan,
            inventory,
            paths["registry"].parent,
            round_dir / "execution",
            cache_dir,
            lock_dir=config.get("lock_dir", "/tmp/simready-device-leases"),
            wall_timeout_s=remaining(budget["wall_seconds"]),
        )
        result.evidence["execution"] = reference(round_dir / "execution/result.json")
        ensure_unchanged(frozen)
        if execution["status"] != "SUCCESS":
            raise ValueError("Plugin execution failed")
        result.candidate = (
            Path(execution["tasks"][final_task]["artifact_dir"]) / "candidate.json"
        )
        result.scorecard, result.defects, result.observations = verify_candidate(
            paths["protocol"],
            result.candidate,
            round_dir / "verification",
            protocol_hash=protocol_hash,
            timeout=remaining(budget["verifier_timeout_s"]),
        )
        result.evidence["after"] = reference(round_dir / "verification/scorecard.json")
        result.comparison = compare_scorecards(before, result.scorecard)
    except (ValueError, OSError, TimeoutError) as error:
        result.error = str(error)
    return result
