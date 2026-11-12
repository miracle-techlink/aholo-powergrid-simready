"""Export usage counters from an explicitly selected local Codex session log.

Only numeric usage and timestamps are exported; never message text or auth data.
This is engineering-session accounting, separate from scene-worker token usage.
"""

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def usage_since(lines, after):
    boundary = timestamp(after)
    previous = None
    previous_time = None
    rows, gaps = [], []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            gaps.append("Incomplete or malformed session-log record")
            continue
        payload = event.get("payload", {})
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        usage = info.get("total_token_usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0 for key in FIELDS
        ):
            gaps.append("Missing cumulative token counters")
            continue
        observed = timestamp(event["timestamp"])
        if observed <= boundary:
            previous, previous_time = usage, observed
            continue
        if previous is None:
            gaps.append("No usage snapshot preceding the request boundary")
        else:
            delta = {key: usage[key] - previous[key] for key in FIELDS}
            if any(value < 0 for value in delta.values()):
                gaps.append("Cumulative usage counters reset or decreased")
            elif delta["total_tokens"]:
                if (
                    delta["total_tokens"]
                    != delta["input_tokens"] + delta["output_tokens"]
                ):
                    gaps.append("Input/output counters disagree with total")
                if delta["cached_input_tokens"] > delta["input_tokens"]:
                    gaps.append("Cached input counters exceed input")
                rows.append(
                    {
                        "usage_update": len(rows) + 1,
                        "timestamp": event["timestamp"],
                        "elapsed_since_request_s": observed - boundary,
                        "wall_interval_including_tools_s": observed
                        - max(previous_time, boundary),
                        **delta,
                        "uncached_input_tokens": delta["input_tokens"]
                        - delta["cached_input_tokens"],
                    }
                )
        previous, previous_time = usage, observed
    return {
        "scope": "Engineering session cumulative-counter deltas; not scene-worker API calls",
        "request_boundary": after,
        "observed_through": rows[-1]["timestamp"] if rows else None,
        "usage_updates": len(rows),
        "totals": {
            key: sum(row[key] for row in rows)
            for key in (*FIELDS, "uncached_input_tokens")
        },
        "counter_continuity_verified": not gaps and bool(rows),
        "gaps": gaps,
        "model_latency_available": False,
        "subsequent_calls_and_final_answer_included": False,
        "notes": [
            "Cached input is a subset of input tokens, not an extra chargeable count.",
            "Reasoning output is a subset of output tokens in this observed log.",
            "Intervals include tool execution and waiting; they are not API latency.",
            "An update is an observed counter change, not a scene repair iteration.",
        ],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-log", type=Path, required=True)
    parser.add_argument("--after", required=True, help="UTC request timestamp")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.session_log.read_bytes()
    report = usage_since(snapshot.decode().splitlines(), args.after)
    report["source_snapshot_sha256"] = hashlib.sha256(snapshot).hexdigest()
    report["source_snapshot_bytes"] = len(snapshot)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "engineering_usage.json").write_text(json.dumps(report, indent=2))
    if report["rows"]:
        with (args.out / "engineering_usage.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(report["rows"][0]))
            writer.writeheader()
            writer.writerows(report["rows"])
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("usage_updates", "totals", "counter_continuity_verified")
            }
        )
    )


if __name__ == "__main__":
    main()
