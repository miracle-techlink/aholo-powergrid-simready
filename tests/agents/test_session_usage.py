import json

from scripts.profile_codex_session import usage_since


def event(second, inputs, outputs, cached):
    return json.dumps(
        {
            "timestamp": f"2026-11-12T00:00:{second:02d}Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": inputs,
                        "output_tokens": outputs,
                        "cached_input_tokens": cached,
                        "reasoning_output_tokens": 0,
                        "total_tokens": inputs + outputs,
                    }
                },
            },
        }
    )


def test_duplicate_usage_updates_are_not_charged_twice():
    rows = [event(1, 100, 10, 60), event(3, 150, 20, 90), event(4, 150, 20, 90)]
    report = usage_since(rows, "2026-11-12T00:00:02Z")
    assert report["counter_continuity_verified"]
    assert report["usage_updates"] == 1
    assert report["totals"]["total_tokens"] == 60
    assert report["totals"]["uncached_input_tokens"] == 20


def test_missing_baseline_and_counter_reset_remain_explicit():
    report = usage_since(
        [event(3, 150, 20, 90), event(4, 50, 5, 0)], "2026-11-12T00:00:02Z"
    )
    assert not report["counter_continuity_verified"]
    assert len(report["gaps"]) == 2
    assert report["usage_updates"] == 0
