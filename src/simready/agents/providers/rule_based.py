"""Offline reference policy for deterministic integration tests and demos."""

from simready.agents.contracts import DecisionResult, RepairDecision
from simready.core.types import JsonObject, PathLike


class RuleBasedProvider:
    def decide(
        self, context: JsonObject, out: PathLike, *, timeout: float
    ) -> DecisionResult:
        proposals = context["proposals"]
        if proposals:
            selected = min(
                proposals,
                key=lambda p: (
                    not bool(p.get("skill_id")),
                    p["priority"],
                    p["proposal_id"],
                ),
            )
            decision = RepairDecision(
                "repair",
                selected["proposal_id"],
                "Prefer validated skill, then registered priority.",
            )
        else:
            decision = RepairDecision(
                "need_evidence", None, "No compatible proposal remains."
            )
        return DecisionResult(
            decision,
            "rules",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "total_tokens": 0,
            },
            0.0,
        )
