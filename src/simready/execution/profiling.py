"""Persist stage timings and explicit token provenance for external workers."""

import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.core.types import JsonObject, PathLike
from simready.execution.processes import run_process


def profile_command(
    argv: Sequence[str],
    *,
    name: str,
    directory: PathLike,
    cwd: PathLike,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    round_index: int | None = None,
    token_mode: str = "unknown",
    usage_file: PathLike | None = None,
    metadata: JsonObject | None = None,
) -> JsonObject:
    """Run a command once; record failures and unknown usage without claiming zero.

    Wall time includes process startup and shutdown. Timings inside a worker are
    separate measurements. A deterministic worker must explicitly declare that
    it makes no LLM calls; model token counts otherwise need provider evidence.
    """
    if token_mode not in {"unknown", "no_llm", "provider"}:
        raise ValueError("Unknown token accounting mode")
    if (token_mode == "provider") != (usage_file is not None):
        raise ValueError("Provider accounting requires exactly one usage file")
    if usage_file is not None and Path(usage_file).exists():
        raise ValueError(
            "Usage file must be new so an earlier call is never charged again"
        )
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    report = {
        "schema_version": 1,
        "stage": name,
        "round_index": round_index,
        "status": "RUNNING",
        "started_unix_s": time.time(),
        "token_mode": token_mode,
        "usage": None,
        "metadata": metadata or {},
    }
    write_json(directory / "profile.json", report)
    try:
        process = run_process(
            list(argv),
            cwd=cwd,
            timeout=timeout_s,
            stdout=directory / "stdout.log",
            stderr=directory / "stderr.log",
            env=None if env is None else dict(env),
        )
        report.update(
            status="SUCCESS" if process.returncode == 0 else "FAILED",
            returncode=process.returncode,
            process_seconds=process.seconds,
        )
    except (OSError, TimeoutError) as error:
        report.update(
            status="TIMEOUT" if isinstance(error, TimeoutError) else "FAILED",
            error=f"{type(error).__name__}: {error}",
        )
    finally:
        report.update(
            ended_unix_s=time.time(),
            wall_seconds=time.perf_counter() - started,
        )
        if token_mode == "no_llm":
            report["usage"] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "total_tokens": 0,
            }
        elif usage_file is not None:
            try:
                usage = read_json(usage_file)
                fields = ("input_tokens", "output_tokens", "total_tokens")
                if not isinstance(usage, dict) or any(
                    type(usage.get(key)) is not int or usage[key] < 0 for key in fields
                ):
                    raise ValueError("Missing or invalid provider token counts")
                if (
                    usage["total_tokens"]
                    != usage["input_tokens"] + usage["output_tokens"]
                ):
                    raise ValueError("Provider token totals are inconsistent")
                cached = usage.get("cached_input_tokens", 0)
                if type(cached) is not int or not 0 <= cached <= usage["input_tokens"]:
                    raise ValueError("Invalid cached token count")
                report["usage"] = {key: usage[key] for key in fields}
                report["usage"]["cached_input_tokens"] = cached
                report["usage_file"] = str(Path(usage_file).resolve())
                report["usage_sha256"] = digest(usage_file)
            except (OSError, ValueError) as error:
                report["usage_error"] = str(error)
        report["usage_complete"] = report["usage"] is not None
        write_json(directory / "profile.json", report)
    return report


def summarize_profiles(records: Sequence[JsonObject]) -> JsonObject:
    """Aggregate costs without confusing summed work with elapsed critical path."""
    completed = [row for row in records if "wall_seconds" in row]
    known = [row["usage"] for row in records if row.get("usage") is not None]
    return {
        "stage_count": len(records),
        "failed_stages": [
            row["stage"] for row in records if row["status"] != "SUCCESS"
        ],
        "stage_wall_sum_seconds": sum(row["wall_seconds"] for row in completed),
        "elapsed_span_seconds": (
            max(row["ended_unix_s"] for row in completed)
            - min(row["started_unix_s"] for row in completed)
            if completed
            else None
        ),
        "known_tokens": {
            key: sum(usage[key] for usage in known)
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "total_tokens",
            )
        },
        "usage_complete": len(known) == len(records),
        "unknown_usage_stages": [
            row["stage"] for row in records if row.get("usage") is None
        ],
    }
