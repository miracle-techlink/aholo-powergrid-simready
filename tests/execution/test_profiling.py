import json
import sys

import pytest

from simready.core.artifacts import read_json, write_json
from simready.execution.profiling import profile_command, summarize_profiles


def test_unknown_usage_is_not_reported_as_complete_zero(tmp_path):
    result = profile_command(
        [sys.executable, "-c", "print('worker')"],
        name="worker",
        directory=tmp_path / "profile",
        cwd=tmp_path,
        timeout_s=10,
    )
    assert result["status"] == "SUCCESS"
    assert result["wall_seconds"] > 0
    assert result["usage"] is None and not result["usage_complete"]
    summary = summarize_profiles([result])
    assert not summary["usage_complete"]
    assert summary["unknown_usage_stages"] == ["worker"]


def test_failed_worker_still_has_cost_and_stderr(tmp_path):
    result = profile_command(
        [
            sys.executable,
            "-c",
            "import sys; print('failure',file=sys.stderr); sys.exit(7)",
        ],
        name="physics",
        directory=tmp_path / "profile",
        cwd=tmp_path,
        timeout_s=10,
        token_mode="no_llm",
    )
    assert result["status"] == "FAILED" and result["returncode"] == 7
    assert result["usage_complete"] and result["usage"]["total_tokens"] == 0
    assert "failure" in (tmp_path / "profile/stderr.log").read_text()
    assert read_json(tmp_path / "profile/profile.json") == result


def test_provider_usage_and_timeout_are_retained(tmp_path):
    usage = dict(
        input_tokens=100, output_tokens=20, cached_input_tokens=50, total_tokens=120
    )
    code = (
        "import json,time; from pathlib import Path; "
        f"Path('usage.json').write_text({json.dumps(json.dumps(usage))}); "
        "time.sleep(10)"
    )
    result = profile_command(
        [sys.executable, "-c", code],
        name="decision",
        directory=tmp_path / "profile",
        cwd=tmp_path,
        timeout_s=0.3,
        token_mode="provider",
        usage_file=tmp_path / "usage.json",
        round_index=2,
    )
    assert result["status"] == "TIMEOUT"
    assert result["round_index"] == 2 and result["usage"] == usage
    assert summarize_profiles([result])["known_tokens"]["total_tokens"] == 120


def test_existing_usage_file_cannot_be_charged_to_a_new_call(tmp_path):
    write_json(tmp_path / "usage.json", {"total_tokens": 100})
    with pytest.raises(ValueError, match="must be new"):
        profile_command(
            [sys.executable, "-c", "pass"],
            name="decision",
            directory=tmp_path / "profile",
            cwd=tmp_path,
            timeout_s=10,
            token_mode="provider",
            usage_file=tmp_path / "usage.json",
        )


def test_missing_provider_usage_stays_unknown(tmp_path):
    result = profile_command(
        [sys.executable, "-c", "pass"],
        name="decision",
        directory=tmp_path / "profile",
        cwd=tmp_path,
        timeout_s=10,
        token_mode="provider",
        usage_file=tmp_path / "missing.json",
    )
    assert result["status"] == "SUCCESS"
    assert result["usage"] is None and "usage_error" in result
