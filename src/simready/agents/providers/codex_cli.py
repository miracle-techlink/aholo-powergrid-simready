"""Codex CLI integration using its own local login; never read/copy auth files."""

import os
import shutil
import subprocess
from pathlib import Path

from simready.agents.contracts import DECISION_SCHEMA, DecisionResult, RepairDecision
from simready.agents.prompting import prompt
from simready.agents.token_usage import normalize_usage
from simready.core.artifacts import loads_json, read_json, write_json
from simready.core.types import JsonObject, PathLike
from simready.execution.processes import run_process


class CodexCliProvider:
    def __init__(self, model=None, executable="codex", max_context_bytes=65536):
        self.model, self.executable, self.max_context_bytes = (
            model,
            executable,
            max_context_bytes,
        )

    def doctor(self):
        executable = shutil.which(self.executable)
        if executable is None:
            return {
                "available": False,
                "authenticated": False,
                "reason": "Codex executable not found",
            }
        status = subprocess.run(
            [executable, "login", "status"], capture_output=True, text=True, timeout=15
        )
        # Deliberately return no raw stdout/stderr, account identifiers or tokens.
        chatgpt = "Logged in using ChatGPT" in status.stdout + status.stderr
        return {
            "available": True,
            "authenticated": status.returncode == 0,
            "chatgpt_login": chatgpt,
        }

    def decide(
        self, context: JsonObject, out: PathLike, *, timeout: float
    ) -> DecisionResult:
        out = Path(out).resolve()
        out.mkdir(parents=True, exist_ok=False)
        text = prompt(context, self.max_context_bytes)
        write_json(out / "schema.json", DECISION_SCHEMA)
        env = dict(os.environ)
        # This provider intentionally uses the user's local Codex account. The
        # separate Responses provider owns API-key mode.
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(key, None)
        command = [
            self.executable,
            "--ask-for-approval",
            "never",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "--disable",
            "multi_agent",
            "exec",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "--output-schema",
            str(out / "schema.json"),
            "--output-last-message",
            str(out / "decision.json"),
            "-C",
            str(out),
        ]
        if self.model:
            command += ["--model", self.model]
        command += ["-"]
        result = run_process(
            command,
            cwd=out,
            timeout=timeout,
            stdout=out / "events.jsonl",
            stderr=out / "stderr.log",
            env=env,
            input_text=text,
        )
        if result.returncode:
            raise RuntimeError(
                f"Codex exited {result.returncode}; inspect the local provider log"
            )
        if (out / "events.jsonl").stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Codex event stream exceeds read limit")
        events = [
            loads_json(line)
            for line in (out / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        completed = [e for e in events if e.get("type") == "turn.completed"]
        if not completed or any(
            e.get("type") in ("turn.failed", "error") for e in events
        ):
            raise RuntimeError("Codex did not complete a successful turn")
        if any(
            e.get("type") == "item.completed"
            and e.get("item", {}).get("type") not in ("agent_message", "reasoning")
            for e in events
        ):
            raise ValueError("Decision provider invoked a tool; reject the turn")
        decision = RepairDecision.parse(read_json(out / "decision.json"), context)
        usage = normalize_usage(completed[-1].get("usage"))
        thread = next(
            (e.get("thread_id") for e in events if e.get("type") == "thread.started"),
            None,
        )
        write_json(out / "usage.json", usage)
        return DecisionResult(decision, "codex", usage, result.seconds, thread)
