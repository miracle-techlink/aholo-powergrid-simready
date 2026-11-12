"""Optional API-key provider; credentials stay in the calling environment."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..artifacts import loads_json, write_json
from .contracts import (
    DECISION_SCHEMA,
    Decision,
    ProviderResult,
    normalize_usage,
    prompt,
)


class ResponsesProvider:
    def __init__(
        self,
        model,
        key_env="OPENAI_API_KEY",
        max_output_tokens=2048,
        max_context_bytes=65536,
    ):
        if not isinstance(model, str) or not model:
            raise ValueError("Responses provider requires an explicit model")
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.model, self.key_env = model, key_env
        self.max_output_tokens, self.max_context_bytes = (
            max_output_tokens,
            max_context_bytes,
        )

    def decide(self, context, out, *, timeout):
        key = os.environ.get(self.key_env)
        if not key:
            raise ValueError(
                f"Missing provider credential environment variable: {self.key_env}"
            )
        out = Path(out)
        out.mkdir(parents=True, exist_ok=False)
        payload = {
            "model": self.model,
            "store": False,
            "input": prompt(context, self.max_context_bytes),
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "scene_repair_decision",
                    "strict": True,
                    "schema": DECISION_SCHEMA,
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            # Do not retry paid requests or persist raw request headers/error bodies.
            raise RuntimeError(
                f"Responses provider HTTP {error.code}; no automatic retry"
            ) from None
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Provider response exceeds read limit")
        value = loads_json(raw)
        if value.get("status") != "completed":
            raise RuntimeError("Responses provider did not complete successfully")
        parts = [
            c["text"]
            for item in value.get("output", [])
            if item.get("type") == "message"
            for c in item.get("content", [])
            if c.get("type") == "output_text"
        ]
        decision = Decision.parse(loads_json("".join(parts)), context)
        usage = normalize_usage(value.get("usage"))
        write_json(
            out / "decision.json",
            {
                "action": decision.action,
                "proposal_id": decision.proposal_id,
                "reason": decision.reason,
            },
        )
        write_json(out / "usage.json", usage)
        return ProviderResult(
            decision, "responses", usage, time.monotonic() - start, value.get("id")
        )
