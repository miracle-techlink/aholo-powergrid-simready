"""Offline reference policy for deterministic integration tests and demos."""

from .contracts import Decision, ProviderResult


class RuleProvider:
    def decide(self, context, out, *, timeout):
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
            decision = Decision(
                "repair",
                selected["proposal_id"],
                "Prefer validated skill, then registered priority.",
            )
        else:
            decision = Decision(
                "need_evidence", None, "No compatible proposal remains."
            )
        return ProviderResult(
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
