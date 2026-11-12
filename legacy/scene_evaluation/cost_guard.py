#!/usr/bin/env python3
"""Offline reservation ledger for future provider integration. Never calls a provider.

Budget units are SUBMISSIONS, not estimated Aholo credits or LLM tokens.
Reservations/unknown outcomes count against the cap and cannot be retried silently.
"""

import hashlib
import json
import sqlite3
import re
from pathlib import Path


def fingerprint(request):
    required = (
        "provider",
        "endpoint",
        "provider_version",
        "input_sha256",
        "parameters",
    )
    if set(request) != set(required):
        raise ValueError(
            f"Request accepts exactly {required}; keep run IDs/timestamps outside the request"
        )
    hashes = request["input_sha256"]
    if (
        not isinstance(hashes, list)
        or not hashes
        or any(
            not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
            for h in hashes
        )
    ):
        raise ValueError(
            "input_sha256 must be a nonempty ordered list of full lowercase SHA256 hashes"
        )
    if not isinstance(request["parameters"], dict) or any(
        not isinstance(request[k], str) or not request[k]
        for k in ("provider", "endpoint", "provider_version")
    ):
        raise ValueError("Invalid provider or parameters")
    return hashlib.sha256(
        json.dumps(
            request, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class CostGuard:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS requests (fingerprint TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, provider_job_id TEXT, artifact TEXT, artifact_sha256 TEXT, actual_credits REAL, currency TEXT, llm_input_tokens INTEGER, llm_output_tokens INTEGER)"
        )
        self.db.commit()

    def reserve(self, request, max_submissions=0):
        """Atomic dedup and cap. Default zero; no submission authorization implied."""
        if type(max_submissions) is not int or max_submissions < 0:
            raise ValueError("max_submissions must be a nonnegative integer")
        key = fingerprint(request)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT status,provider_job_id,artifact,artifact_sha256 FROM requests WHERE fingerprint=?",
                (key,),
            ).fetchone()
            if row:
                action = "RECONCILE_EXISTING"
                if row[0] == "succeeded" and row[2] and Path(row[2]).is_file():
                    from legacy.scene_evaluation.bench import sha256

                    if sha256(row[2]) == row[3]:
                        action = "REUSE_ARTIFACT"
                self.db.commit()
                return {
                    "action": action,
                    "fingerprint": key,
                    "status": row[0],
                    "provider_job_id": row[1],
                    "artifact": row[2],
                }
            count = self.db.execute("SELECT count(*) FROM requests").fetchone()[0]
            if count >= max_submissions:
                self.db.commit()
                return {
                    "action": "BUDGET_BLOCKED",
                    "fingerprint": key,
                    "reserved_submissions": count,
                }
            self.db.execute(
                "INSERT INTO requests(fingerprint,request,status) VALUES (?,?,?)",
                (key, json.dumps(request, sort_keys=True), "reserved"),
            )
            self.db.commit()
            return {"action": "RESERVED", "fingerprint": key}
        except Exception:
            self.db.rollback()
            raise

    def record(
        self, key, status, provider_job_id=None, artifact=None, actual_credits=None
    ):
        if status not in ("submitted", "unknown", "failed", "succeeded"):
            raise ValueError("Invalid provider outcome")
        digest = None
        if status == "succeeded":
            if not artifact or not provider_job_id:
                raise ValueError(
                    "Success needs a real provider job id and existing artifact"
                )
            from legacy.scene_evaluation.bench import sha256

            artifact = str(Path(artifact).resolve())
            digest = sha256(artifact)
        if actual_credits is not None and (
            not isinstance(actual_credits, (float, int))
            or not 0 <= actual_credits < float("inf")
        ):
            raise ValueError(
                "Actual credits must be finite and nonnegative; unknown is null"
            )
        cursor = self.db.execute(
            "UPDATE requests SET status=?,provider_job_id=COALESCE(?,provider_job_id),artifact=COALESCE(?,artifact),artifact_sha256=COALESCE(?,artifact_sha256),actual_credits=COALESCE(?,actual_credits) WHERE fingerprint=?",
            (status, provider_job_id, artifact, digest, actual_credits, key),
        )
        self.db.commit()
        if cursor.rowcount != 1:
            raise ValueError("Unknown reservation")

    def summary(self):
        rows = self.db.execute("SELECT status,actual_credits FROM requests").fetchall()
        return {
            "reserved_submissions": len(rows),
            "actual_credits": sum(r[1] for r in rows)
            if rows and all(r[1] is not None for r in rows)
            else None,
            "unknown_credit_entries": sum(r[1] is None for r in rows),
            "provider_integration": "NOT_CONNECTED",
        }
