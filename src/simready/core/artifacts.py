"""Shared IO without importing CUDA or numerical libraries."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


def loads_json(text):
    """Reject duplicate keys and nonfinite constants at every external boundary."""

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key: {key}")
            value[key] = item
        return value

    def constant(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("JSON number exceeds finite float range")
        return result

    return json.loads(
        text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating
    )


def read_json(path):
    return loads_json(Path(path).read_text())


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def scene_dependency_hashes(directory):
    """Bind evidence to geometry, occupancy, provenance and texture contents."""
    directory = Path(directory)
    suffixes = {
        ".usd",
        ".usda",
        ".usdc",
        ".json",
        ".npy",
        ".npz",
        ".png",
        ".jpg",
        ".jpeg",
    }
    return {
        str(p.relative_to(directory)): digest(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.suffix.lower() in suffixes
    }


def object_digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def write_json(path, value):
    """Unique temporary files avoid collisions between concurrent writers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
