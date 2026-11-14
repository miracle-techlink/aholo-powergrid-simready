"""Content-addressed artifact verification shared by scheduler and workers."""

from pathlib import Path

from simready.core.artifacts import digest, read_json


def hash_outputs(directory, names):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Missing or escaping output directory")
    directory = directory.resolve()
    result = {}
    for name in names:
        path = directory / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(directory)
            or not path.is_file()
        ):
            raise ValueError(f"Missing or escaping output: {name}")
        result[name] = digest(path)
    return result


def cache_hit(cache, key, outputs):
    directory = cache / key
    try:
        manifest = directory / "manifest.json"
        if directory.is_symlink() or manifest.is_symlink():
            return None
        record = read_json(manifest)
        hashes = hash_outputs(directory / "files", outputs)
        if record["key"] == key and record["outputs"] == hashes:
            return {"artifact_dir": str(directory / "files"), "outputs": hashes}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
