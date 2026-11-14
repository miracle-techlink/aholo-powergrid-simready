"""Verified stage receipts and explicit identities; no worker or resource scheduling.

Legacy callers use a run identity and ``stages/<name>.json``. Explicit stage
identities use immutable ``stages/<name>/<identity>.json`` receipts; the old path
is a compatibility index. The caller owns run locking, attempts, dependencies,
and the nonsecret recipe. Credentials must never be included in recipes.
"""

import copy
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.types import JsonObject
from simready.execution.processes.local import (
    ProcessCancelledError,
    ProcessTimeoutError,
)

IDENTITY_VERSION = "v1.0"
_SHA256 = re.compile(r"[a-f0-9]{64}")
_STAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _json_value(value, active=None):
    """Accept JSON types only: no implicit tuple/key/scalar coercions or NaN."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Stage identity must contain finite JSON numbers")
        return
    if type(value) not in (dict, list):
        raise ValueError("Stage identity requires strictly JSON-serializable values")
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError("Stage identity cannot contain cyclic values")
    active.add(id(value))
    try:
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise ValueError("Stage identity object keys must be strings")
            children = value.values()
        else:
            children = value
        for child in children:
            _json_value(child, active)
    finally:
        active.remove(id(value))


def _hash_mapping(value, label, *, source_root=None, references=False):
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not name or "\x00" in name for name in value
    ):
        raise ValueError(f"{label} must map logical names to SHA256 digests")
    normalized = {}
    for name, item in value.items():
        expected = item
        if references and isinstance(item, dict):
            if set(item) != {"path", "sha256"} or not isinstance(item["path"], str):
                raise ValueError("Implementation references require path and sha256")
            if source_root is None:
                raise ValueError(
                    "Implementation path references require an explicit source_root"
                )
            root = Path(source_root).resolve()
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(
                    "Implementation reference escaped source_root or is missing"
                )
            expected = item["sha256"]
            if digest(path) != expected:
                raise ValueError("Implementation reference changed")
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise ValueError(f"{label} requires lowercase SHA256 digests")
        normalized[name] = expected
    return normalized


def stage_identity(recipe, *, source_root=None):
    """Hash provider/version/config, inputs, runtime, implementation and dependencies.

    Required keys are ``provider``, ``version``, ``config``, ``inputs``,
    ``runtime`` and ``implementation``; ``dependencies`` is optional. Input and
    dependency values are SHA256 strings. Implementation values may also be
    ``{path, sha256}`` references checked under an explicit source_root; paths
    do not enter the identity, allowing identical source checkouts to relocate.
    Config/runtime contain nonsecret JSON settings; this function stores no recipe.
    """
    _json_value(recipe)
    required = {"provider", "version", "config", "inputs", "runtime", "implementation"}
    if (
        not isinstance(recipe, dict)
        or not required <= recipe.keys()
        or set(recipe) - required - {"dependencies"}
    ):
        raise ValueError("Stage identity recipe has missing or unknown fields")
    for name in ("provider", "version"):
        if not isinstance(recipe[name], str) or not recipe[name].strip():
            raise ValueError(f"Stage identity requires a nonempty {name}")
    if not isinstance(recipe["config"], dict):
        raise ValueError("Stage config must be a JSON object")
    if not isinstance(recipe["runtime"], (str, dict)) or not recipe["runtime"]:
        raise ValueError("Stage runtime requires an explicit nonempty identity")
    normalized = copy.deepcopy(recipe)
    normalized["inputs"] = _hash_mapping(recipe["inputs"], "inputs")
    normalized["dependencies"] = _hash_mapping(
        recipe.get("dependencies", {}), "dependencies"
    )
    normalized["implementation"] = _hash_mapping(
        recipe["implementation"],
        "implementation",
        source_root=source_root,
        references=True,
    )
    if not normalized["implementation"]:
        raise ValueError("Stage identity requires implementation source hashes")
    return object_digest({"identity_version": IDENTITY_VERSION, "recipe": normalized})


def checked_artifact(root: Path, item: JsonObject) -> Path:
    """Resolve one nonempty run-local artifact and verify its recorded content."""
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or Path(item["path"]).is_absolute()
    ):
        raise ValueError("Artifact paths must be relative to their run")
    if type(item.get("bytes")) is not int or item["bytes"] <= 0:
        raise ValueError("Stage artifacts must be nonempty files")
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Stage artifact escaped its run or is missing")
    if path.stat().st_size != item["bytes"] or digest(path) != item.get("sha256"):
        raise ValueError(f"Stage artifact changed: {item['path']}")
    return path


def artifact_record(root: Path, path: Path) -> JsonObject:
    """Describe an existing run-local output without publishing a stage receipt."""
    path = Path(path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Stage outputs must be files inside the run")
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def failure_status(error):
    if isinstance(error, ProcessTimeoutError):
        return "TIMEOUT"
    if isinstance(error, ProcessCancelledError):
        return "CANCELLED"
    return "FAILED" if isinstance(error, Exception) else "INTERRUPTED"


def _write_once(path, value):
    """Atomically publish a complete receipt without replacing any prior success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".receipt-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class VideoStages:
    """Store stage evidence; caller serializes its run and chooses fresh attempts."""

    def __init__(self, root, identity):
        root = Path(root)
        if root.is_symlink():
            raise ValueError("Stage run directory must not be a symlink")
        self.root, self.identity, self.history = root.resolve(), identity, []

    def _receipt_path(self, relative):
        path = self.root / relative
        if any(
            part.is_symlink()
            for part in (path, *path.parents)
            if part.is_relative_to(self.root)
        ):
            raise ValueError("Stage receipt paths must not traverse symlinks")
        return path

    def run(self, name, action, validate=None, *, stage_identity=None):
        if not isinstance(name, str) or _STAGE_NAME.fullmatch(name) is None:
            raise ValueError("Stage name must be a plain directory identifier")
        explicit = stage_identity is not None
        if explicit and (
            not isinstance(stage_identity, str)
            or _SHA256.fullmatch(stage_identity) is None
        ):
            raise ValueError("stage_identity must be a lowercase SHA256 digest")
        identity = stage_identity if explicit else self.identity
        reused = False
        started = time.monotonic()
        try:
            index = self._receipt_path(Path("stages") / f"{name}.json")
            receipt = (
                self._receipt_path(Path("stages") / name / f"{identity}.json")
                if explicit
                else index
            )
            reused = receipt.exists()
            previous = read_json(index) if explicit and index.exists() else None
            if (
                explicit
                and not reused
                and isinstance(previous, dict)
                and previous.get("stage_identity") == identity
            ):
                raise ValueError(
                    "Indexed immutable stage receipt is missing; preserve evidence for inspection"
                )
            if reused:
                record = read_json(receipt)
                if not isinstance(record, dict) or record.get("identity") != identity:
                    raise ValueError("Stage identity differs from the current run plan")
            else:
                print(f"[{name}] RUNNING", flush=True)
                result, paths = action()
                record = {
                    "identity": identity,
                    "name": name,
                    "wall_s": time.monotonic() - started,
                    "result": result,
                    "artifacts": [artifact_record(self.root, path) for path in paths],
                }
                if explicit:
                    record.update(stage_identity=identity, plan_identity=self.identity)
            if not isinstance(record.get("artifacts"), list) or not record["artifacts"]:
                raise ValueError("A successful stage must provide verifiable artifacts")
            for item in record["artifacts"]:
                path = checked_artifact(self.root, item)
                if path.relative_to(self.root).parts[0] in {"stages", "stage-failures"}:
                    raise ValueError(
                        "Stage artifacts cannot overwrite ledger receipt directories"
                    )
            if record.get("name") != name or not isinstance(record.get("result"), dict):
                raise ValueError("Stage name or result is invalid")
            if explicit and record.get("stage_identity") != identity:
                raise ValueError("Canonical stage receipt has inconsistent identity")
            if validate is not None:
                validate(
                    copy.deepcopy(record["result"]), copy.deepcopy(record["artifacts"])
                )
            if not reused:
                if explicit:
                    _write_once(receipt, record)
                else:
                    write_json(receipt, record)
            if explicit:
                # Keep the legacy path readable. The immutable receipt remains
                # authoritative when a different run plan selects an older stage.
                view = {
                    **record,
                    "receipt": str(receipt.relative_to(self.root)),
                    "receipt_sha256": digest(receipt),
                }
                if reused and previous is not None:
                    if (
                        isinstance(previous, dict)
                        and previous.get("stage_identity") == identity
                        and previous != view
                    ):
                        raise ValueError(
                            "Stage compatibility index differs from its immutable receipt"
                        )
                if previous is not None and (
                    not isinstance(previous, dict) or "stage_identity" not in previous
                ):
                    archive = self._receipt_path(
                        Path("stages") / name / f"legacy-{object_digest(previous)}.json"
                    )
                    if not archive.exists():
                        _write_once(archive, previous)
                    elif read_json(archive) != previous:
                        raise ValueError("Archived legacy stage receipt changed")
                write_json(index, view)
        except BaseException as error:
            failure = {
                "identity": identity,
                "name": name,
                "status": failure_status(error),
                "reused": reused,
                "wall_s": time.monotonic() - started,
                "error": str(error) or type(error).__name__,
            }
            if explicit:
                failure.update(stage_identity=identity, plan_identity=self.identity)
            try:
                path = self._receipt_path(
                    Path("stage-failures") / name / f"{time.time_ns()}.json"
                )
                write_json(path, failure)
            except BaseException as receipt_error:
                failure["receipt_error"] = type(receipt_error).__name__
                if hasattr(error, "add_note"):
                    error.add_note(
                        f"Stage failure receipt could not be written: {type(receipt_error).__name__}"
                    )
            self.history.append(failure)
            raise
        elapsed = 0.0 if reused else record["wall_s"]
        history = {"name": name, "reused": reused, "wall_s": elapsed}
        if explicit:
            history["stage_identity"] = identity
        self.history.append(history)
        status = "REUSED (hash verified)" if reused else f"COMPLETE ({elapsed:.2f}s)"
        print(f"[{name}] {status}", flush=True)
        return record["result"]


StageLedger = VideoStages
