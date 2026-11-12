"""Validated workflow inputs, budgets and evaluator policy identity."""

from pathlib import Path
from typing import NamedTuple

from simready.core.artifacts import digest, object_digest, read_json
from simready.core.types import JsonObject, PathLike
from simready.core.validation import number
from simready.evaluation.protocol import validate_protocol


class WorkflowInputs(NamedTuple):
    """Resolved inputs from a schema-versioned workflow document."""

    paths: dict[str, Path]
    protocol: JsonObject
    protocol_sha256: str
    policy_key: str


def load_workflow_config(config: JsonObject, base: PathLike) -> WorkflowInputs:
    """Validate a v1 workflow document and resolve its evidence identities."""
    if config.get("schema_version") != 1:
        raise ValueError("Campaign requires schema_version=1")
    for name in ("scene_id", "family"):
        if not isinstance(config.get(name), str) or not config[name]:
            raise ValueError(f"Campaign requires {name}")
    if config.get("role") not in ("development", "validation"):
        raise ValueError("Agent campaigns cannot consume sealed validation")
    paths = {
        key: (Path(base) / config[key]).resolve()
        for key in ("protocol", "candidate", "registry", "inventory", "memory")
    }
    protocol = validate_protocol(read_json(paths["protocol"]))
    if protocol["phase"] != "development":
        raise ValueError("Sealed benchmark feedback cannot enter an agent campaign")
    budget = config["budget"]
    for key in ("max_rounds", "max_agent_calls", "max_total_tokens"):
        if type(budget.get(key)) is not int or budget[key] < 1:
            raise ValueError(f"Budget requires positive integer {key}")
    for key in ("wall_seconds", "agent_timeout_s", "verifier_timeout_s"):
        number(budget[key], key, 0.001)
    if type(config.get("auto_promote_skills", False)) is not bool:
        raise ValueError("auto_promote_skills must be boolean")
    benchmark_dir = Path(__file__).resolve().parents[1] / "evaluation"
    verifier_identity = {
        str(p.relative_to(benchmark_dir.parent)): digest(p)
        for p in [
            *sorted(benchmark_dir.glob("*.py")),
            *sorted((benchmark_dir.parent / "core").glob("*.py")),
        ]
    }
    policy = object_digest(
        {
            "verifier": verifier_identity,
            "benchmark_id": protocol["benchmark_id"],
            "profile": protocol["profile"],
            "checks": protocol["checks"],
            "asset_policy": protocol.get("asset_policy"),
        }
    )
    return WorkflowInputs(paths, protocol, digest(paths["protocol"]), policy)
