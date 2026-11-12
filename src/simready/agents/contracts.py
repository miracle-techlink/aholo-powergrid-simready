"""The agent chooses from host-generated proposals, never arbitrary shell text."""

from dataclasses import dataclass
from typing import Protocol

from simready.core.types import JsonObject, PathLike

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["repair", "stop", "need_evidence"]},
        "proposal_id": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "proposal_id", "reason"],
}


@dataclass(frozen=True)
class RepairDecision:
    action: str
    proposal_id: str | None
    reason: str

    @classmethod
    def parse(cls, value: JsonObject, context: JsonObject) -> "RepairDecision":
        if not isinstance(value, dict) or set(value) != {
            "action",
            "proposal_id",
            "reason",
        }:
            raise ValueError("Invalid agent decision fields")
        if value["action"] not in ("repair", "stop", "need_evidence"):
            raise ValueError("Unknown agent action")
        if not isinstance(value["reason"], str) or not 0 < len(value["reason"]) <= 4000:
            raise ValueError("Decision needs a bounded explanation")
        proposal = value["proposal_id"]
        if value["action"] == "repair":
            if not isinstance(proposal, str) or proposal not in {
                p["proposal_id"] for p in context["proposals"]
            }:
                raise ValueError("Agent selected an unregistered proposal")
        elif proposal is not None:
            raise ValueError("Only repair decisions may select a proposal")
        return cls(**value)


@dataclass(frozen=True)
class DecisionResult:
    decision: RepairDecision
    provider: str
    usage: dict | None
    seconds: float
    request_id: str | None = None


class DecisionProvider(Protocol):
    def decide(
        self, context: JsonObject, out: PathLike, *, timeout: float
    ) -> DecisionResult: ...
