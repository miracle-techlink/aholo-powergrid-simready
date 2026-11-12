import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from simready.agents.providers.codex_cli import CodexCliProvider
from simready.agents.providers.openai_responses import OpenAIResponsesProvider
from simready.core.artifacts import write_json
from simready.execution.processes import ProcessResult, ProcessTimeoutError

CONTEXT = {"proposals": [{"proposal_id": "registered"}]}
DECISION = {
    "action": "repair",
    "proposal_id": "registered",
    "reason": "Use the registered proposal",
}
USAGE = {"input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 4}


def test_codex_contract_environment_and_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-copy")
    monkeypatch.setenv("CODEX_API_KEY", "do-not-copy-either")

    def run(command, **kw):
        assert "--ignore-user-config" in command and "--ephemeral" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert all(key not in kw["env"] for key in ("OPENAI_API_KEY", "CODEX_API_KEY"))
        assert command.count("--disable") == 3 and command[-1] == "-"
        assert "registered" in kw["input_text"]
        Path(kw["stdout"]).write_text(
            "\n".join(
                json.dumps(e)
                for e in [
                    {"type": "thread.started", "thread_id": "thread-fixture"},
                    {"type": "item.completed", "item": {"type": "agent_message"}},
                    {"type": "turn.completed", "usage": USAGE},
                ]
            )
        )
        write_json(Path(command[command.index("--output-last-message") + 1]), DECISION)
        return ProcessResult(0, 0.1)

    monkeypatch.setattr("simready.agents.providers.codex_cli.run_process", run)
    response = CodexCliProvider().decide(CONTEXT, tmp_path / "call", timeout=2)
    assert (
        response.usage["total_tokens"] == 15
        and response.usage["cached_input_tokens"] == 4
    )
    assert response.request_id == "thread-fixture"
    assert "do-not-copy" not in "".join(
        p.read_text() for p in (tmp_path / "call").iterdir()
    )


@pytest.mark.parametrize(
    "event",
    [
        {"type": "turn.failed"},
        {"type": "error"},
        {"type": "item.completed", "item": {"type": "command_execution"}},
    ],
)
def test_codex_rejects_failure_or_tool_events(tmp_path, monkeypatch, event):
    def run(command, **kw):
        Path(kw["stdout"]).write_text(
            json.dumps(event)
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": USAGE})
        )
        write_json(Path(kw["cwd"]) / "decision.json", DECISION)
        return ProcessResult(0, 0.1)

    monkeypatch.setattr("simready.agents.providers.codex_cli.run_process", run)
    with pytest.raises((RuntimeError, ValueError)):
        CodexCliProvider().decide(CONTEXT, tmp_path / "call", timeout=2)


def test_codex_timeout_and_doctor_redaction(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise ProcessTimeoutError(1, 1)

    monkeypatch.setattr("simready.agents.providers.codex_cli.run_process", timeout)
    with pytest.raises(TimeoutError):
        CodexCliProvider().decide(CONTEXT, tmp_path / "call", timeout=1)
    monkeypatch.setattr("shutil.which", lambda path: "/bin/codex-fixture")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "Logged in using ChatGPT secret", ""
        ),
    )
    assert CodexCliProvider().doctor() == {
        "available": True,
        "authenticated": True,
        "chatgpt_login": True,
    }


def test_responses_request_and_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")

    def open_request(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer fixture-key"
        payload = json.loads(request.data)
        assert payload["store"] is False and "tools" not in payload
        assert payload["text"]["format"]["strict"] is True
        return io.BytesIO(
            json.dumps(
                {
                    "status": "completed",
                    "id": "fixture-response",
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "input_tokens_details": {"cached_tokens": 4},
                    },
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps(DECISION)}
                            ],
                        }
                    ],
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    result = OpenAIResponsesProvider("explicit-model").decide(
        CONTEXT, tmp_path / "call", timeout=2
    )
    assert (
        result.usage["total_tokens"] == 15
        and result.decision.proposal_id == "registered"
    )
    assert "fixture-key" not in "".join(
        p.read_text() for p in (tmp_path / "call").iterdir()
    )


def test_responses_http_error_not_retried_or_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError(
            "https://api.openai.com/v1/responses", 429, "SECRET error body", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 429") as error:
        OpenAIResponsesProvider("explicit-model").decide(
            CONTEXT, tmp_path / "call", timeout=2
        )
    assert "SECRET" not in str(error.value) and len(calls) == 1
