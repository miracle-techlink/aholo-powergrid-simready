"""Source-bound reconstruction receipts with explicit submission and offline reuse.

This stage delivers PLY/SPZ files, not a calibrated or interactive scene. The
supervised cloud worker has one wall deadline covering upload, polling and
downloads. Local hashing/copying is synchronous. No requests occur on import.

Reusable manifests use schema_version=1, status=COMPLETE, source_video_sha256
and artifacts=[{kind: ply|spz, path: relative_path, sha256: ..., bytes: ...}].
Artifact paths must remain inside the manifest directory after resolving links.
"""

import fcntl
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.processes.supervision import supervise_process

KINDS = {"ply": "splats.ply", "spz": "splats.spz"}


def _verified_artifacts(directory, records):
    if not isinstance(records, list) or not records:
        raise ValueError("Reconstruction manifest needs artifact records")
    directory = Path(directory).resolve()
    verified = {}
    for record in records:
        if not isinstance(record, dict) or record.get("kind") not in KINDS:
            raise ValueError("Reconstruction supports only declared PLY/SPZ artifacts")
        kind = record["kind"]
        if kind in verified:
            raise ValueError("Repeated reconstruction artifact kind")
        raw = record.get("path")
        if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
            raise ValueError("Artifact paths must be relative to their manifest")
        path = (directory / raw).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError(
                "Reconstruction artifact is missing or leaves its manifest directory"
            )
        if (
            type(record.get("bytes")) is not int
            or record["bytes"] <= 0
            or path.stat().st_size != record["bytes"]
            or digest(path) != record.get("sha256")
        ):
            raise ValueError("Reconstruction artifact size or hash changed")
        verified[kind] = {**record, "resolved_path": path}
    if "ply" not in verified:
        raise ValueError("Reconstruction requires a verified PLY artifact")
    return verified


def _verified_manifest(path, source_hash):
    manifest = read_json(path)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or manifest.get("status") != "COMPLETE"
        or manifest.get("source_video_sha256") != source_hash
    ):
        raise ValueError(
            "Reconstruction manifest is incomplete or belongs to another video"
        )
    return manifest, _verified_artifacts(Path(path).parent, manifest.get("artifacts"))


def _provider_receipt(out, source_hash):
    receipt = read_json(out / "receipt.json")
    if (
        not isinstance(receipt, dict)
        or receipt.get("video_sha256") != source_hash
        or not isinstance(receipt.get("world_id"), str)
        or not receipt["world_id"]
    ):
        raise ValueError("Aholo receipt is invalid or belongs to another video")
    return receipt


def _provider_artifacts(out, job):
    status = read_json(out / "status.json")
    assets = read_json(out / "assets.json")
    if (
        not isinstance(status, dict)
        or not isinstance(assets, dict)
        or status.get("world_id") != job["world_id"]
        or str(status.get("status", "")).upper() not in {"SUCCEEDED", "SUCCESS", "DONE"}
        or assets.get("world_id") != job["world_id"]
        or not isinstance(assets.get("assets"), list)
    ):
        raise ValueError(
            "Cloud completion and artifact receipts do not identify the same successful job"
        )
    records = []
    for item in assets["assets"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid downloaded reconstruction artifact")
        kind = next(
            (kind for kind, filename in KINDS.items() if filename == item.get("path")),
            None,
        )
        if kind is None:
            raise ValueError("Unexpected downloaded reconstruction artifact")
        records.append({**item, "kind": kind})
    return _verified_artifacts(out, records)


def _check_output(out):
    if out.exists():
        if not out.is_dir():
            raise ValueError("Reconstruction output must be a directory")
        if any(path.is_symlink() for path in out.iterdir()):
            raise ValueError(
                "Reconstruction output must not contain symlinked state or artifacts"
            )


def _cached_result(completion, source_hash, imported):
    manifest, verified = _verified_manifest(completion, source_hash)
    if imported and {k: v["sha256"] for k, v in imported[1].items()} != {
        k: v["sha256"] for k, v in verified.items()
    }:
        raise ValueError(
            "Requested artifact manifest differs from the cached reconstruction"
        )
    return _result(
        manifest,
        verified,
        completion,
        cache_hit=True,
        new_submission=False,
        resumed=False,
    )


def _result(manifest, artifacts, manifest_path, *, cache_hit, new_submission, resumed):
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "ply_path": str(artifacts["ply"]["resolved_path"]),
        "ply_sha256": artifacts["ply"]["sha256"],
        "cache_hit": cache_hit,
        "new_submission": new_submission,
        "resumed": resumed,
    }


def _publish(out, source_hash, artifacts, **metadata):
    manifest = {
        "schema_version": 1,
        "status": "COMPLETE",
        "source_video_sha256": source_hash,
        "artifacts": [
            {key: item[key] for key in ("kind", "path", "sha256", "bytes")}
            for item in artifacts.values()
        ],
        "geometry_validated": False,
        "scale_calibrated": False,
        "cloud_cost_usd": None,
        **metadata,
    }
    path = out / "reconstruction.json"
    write_json(path, manifest)
    return _result(
        manifest,
        artifacts,
        path,
        **{key: metadata[key] for key in ("cache_hit", "new_submission", "resumed")},
    )


def _import_artifacts(out, records):
    copied = {}
    for kind, item in records.items():
        target = out / KINDS[kind]
        if target.is_symlink():
            raise ValueError("Refusing a symlink at an output artifact path")
        if target.exists():
            if not target.is_file() or digest(target) != item["sha256"]:
                raise ValueError(
                    "Refusing to overwrite a different reconstruction artifact"
                )
        else:
            descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=out)
            os.close(descriptor)
            partial = Path(temporary)
            try:
                shutil.copyfile(item["resolved_path"], partial)
                if (
                    partial.stat().st_size != item["bytes"]
                    or digest(partial) != item["sha256"]
                ):
                    raise ValueError("Reconstruction artifact changed while copying")
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        copied[kind] = {**item, "path": target.name, "resolved_path": target}
    return copied


def prepare_reconstruction(
    video,
    out,
    *,
    artifact_manifest=None,
    submit=False,
    timeout=7200,
):
    """Return a verified local reconstruction receipt or fail without resubmitting.

    A complete local manifest is reused without an API key or network access.
    Existing Aholo receipts may resume with submit=False. A new job requires
    submit=True; an unresolved submission intent always blocks another POST.
    Cloud failures keep receipts, verified downloads and sanitized worker logs.
    """
    if type(submit) is not bool:
        raise ValueError("submit must be an explicit boolean")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("Reconstruction timeout must be finite and positive")
    video, out = Path(video).expanduser().resolve(), Path(out).expanduser().resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError("Reconstruction needs a nonempty source video")
    if video.is_relative_to(out):
        raise ValueError("Reconstruction output must not contain the source video")
    source_hash = digest(video)
    _check_output(out)
    completion = out / "reconstruction.json"
    imported = None
    if artifact_manifest is not None:
        artifact_manifest = Path(artifact_manifest).expanduser().resolve()
        imported = _verified_manifest(artifact_manifest, source_hash)
    if completion.exists():
        return _cached_result(completion, source_hash, imported)
    receipt = out / "receipt.json"
    if imported is None:
        if receipt.exists():
            _provider_receipt(out, source_hash)
        elif (out / "submit_intent.json").exists():
            raise RuntimeError(
                "Submission outcome unknown; recover the existing server receipt before retrying"
            )
        elif not submit:
            raise PermissionError(
                "A new cloud reconstruction requires explicit submit=True (--submit-reconstruction)"
            )
    out.mkdir(parents=True, exist_ok=True)
    _check_output(out)
    with (out / "reconstruction.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if completion.exists():
            return _cached_result(completion, source_hash, imported)
        if imported is not None:
            if receipt.exists() or (out / "submit_intent.json").exists():
                raise ValueError(
                    "Use a fresh output for importing artifacts into an existing cloud job"
                )
            artifacts = _import_artifacts(out, imported[1])
            if digest(video) != source_hash:
                raise ValueError(
                    "Source video changed while importing reconstruction artifacts"
                )
            return _publish(
                out,
                source_hash,
                artifacts,
                provider=imported[0].get("provider", "provided"),
                provenance="user_supplied_artifact_manifest",
                imported_manifest_sha256=digest(artifact_manifest),
                cache_hit=True,
                new_submission=False,
                resumed=False,
            )
        resumed = receipt.exists()
        if resumed:
            job = _provider_receipt(out, source_hash)
            # A completed legacy job is already enough to prove artifact
            # identity. Do not require credentials or re-fetch signed URLs.
            if (out / "status.json").exists() and (out / "assets.json").exists():
                verified = _provider_artifacts(out, job)
                if digest(video) != source_hash:
                    raise ValueError(
                        "Source video changed while verifying reconstruction artifacts"
                    )
                return _publish(
                    out,
                    source_hash,
                    verified,
                    provider="aholo",
                    provenance="cloud_job_receipt",
                    world_id=job["world_id"],
                    provider_receipt_sha256=digest(receipt),
                    cache_hit=True,
                    new_submission=False,
                    resumed=True,
                )
        if not os.environ.get("AHOLO_API_KEY", "").strip():
            raise ValueError(
                "Cloud reconstruction or monitoring requires AHOLO_API_KEY; verified local artifacts can be reused offline"
            )
        command = [
            sys.executable,
            "-m",
            "simready.ingestion.reconstruction.aholo_reconstruction",
            "--video",
            str(video),
            "--out",
            str(out),
            "--poll-timeout",
            str(timeout),
            *(["--submit"] if submit else []),
        ]
        budget = min(
            timeout,
            float(os.environ.get("SIMREADY_JOB_DEADLINE_UNIX_S", time.time() + timeout))
            - time.time(),
        )
        if budget <= 0:
            raise TimeoutError(
                "Reconstruction deadline exhausted; existing receipt retained"
            )
        supervisor = out / f"supervisor_{time.time_ns()}"
        report = supervise_process(
            command, cwd=Path.cwd(), directory=supervisor, timeout=budget
        )
        if report["status"] == "TIMEOUT":
            raise TimeoutError(
                "Reconstruction deadline exhausted; resume the retained receipt without resubmitting"
            )
        if report["status"] != "SUCCESS":
            raise RuntimeError(
                "Reconstruction worker failed; inspect the retained supervisor logs. Cloud monitoring requires AHOLO_API_KEY; unresolved submissions are never retried automatically."
            )
        job = _provider_receipt(out, source_hash)
        verified = _provider_artifacts(out, job)
        if digest(video) != source_hash:
            raise ValueError("Source video changed during reconstruction")
        return _publish(
            out,
            source_hash,
            verified,
            provider="aholo",
            provenance="cloud_job_receipt",
            world_id=job["world_id"],
            provider_receipt_sha256=digest(receipt),
            cache_hit=False,
            new_submission=not resumed,
            resumed=resumed,
        )
