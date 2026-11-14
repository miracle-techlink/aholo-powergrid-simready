"""Validate workflow fields and input paths without planning or execution."""

from pathlib import Path


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"Unknown or malformed {label} fields")
    return value


def file_path(value, base):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a nonempty file path")
    path = (base / Path(value).expanduser()).resolve()
    if not path.is_file():
        raise ValueError(f"Missing pipeline input: {path}")
    return path
