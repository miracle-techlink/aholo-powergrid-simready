"""Create and verify immutable file references."""

from pathlib import Path

from simready.core.artifacts import digest
from simready.core.validation import require


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path)}


def resolve_ref(ref, root):
    require(isinstance(ref, dict), "File reference must be an object")
    require(
        isinstance(ref.get("path"), str) and ref["path"], "File reference needs a path"
    )
    path = (Path(root) / ref["path"]).resolve()
    require(path.is_file(), f"Missing file: {path}")
    require(digest(path) == ref.get("sha256"), f"SHA256 mismatch: {path}")
    return path


def ensure_unchanged(references):
    for ref in references:
        if digest(ref["path"]) != ref["sha256"]:
            raise ValueError("Frozen campaign evidence changed")
