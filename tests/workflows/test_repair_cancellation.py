"""Cancelled or failed agent calls leave honest campaign and token receipts."""

import pytest

from examples.repair_workflow.run_learning_demo import prepare
from simready.agents.providers.rule_based import RuleBasedProvider
from simready.core.artifacts import read_json
from simready.workflows import run_repair_workflow


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError, TimeoutError])
def test_provider_failure_keeps_unknown_usage_and_terminal_receipt(tmp_path, error):
    class FailedProvider(RuleBasedProvider):
        def decide(self, *args, **kwargs):
            raise error("injected provider failure")

    config, base = prepare(tmp_path, 0)
    out = tmp_path / "run"

    def run():
        return run_repair_workflow(
            config, base_dir=base, output_dir=out, decision_provider=FailedProvider()
        )

    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            run()
    else:
        run()
    report = read_json(out / "result.json")
    assert (
        report["status"]
        == {
            KeyboardInterrupt: "INTERRUPTED",
            RuntimeError: "FAILED",
            TimeoutError: "BUDGET_EXHAUSTED",
        }[error]
    )
    assert report["agent_calls"] == 1 and report["usage_complete"] is False
    assert report["rounds"] == []
    assert not (out / "round_000/execution").exists()
