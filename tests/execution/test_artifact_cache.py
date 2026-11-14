"""Cache evidence must remain owned files, not aliases to another run."""

import json

import pytest

from simready.core.artifacts import digest, write_json
from simready.execution.artifact_cache import cache_hit, hash_outputs


@pytest.fixture
def entry(tmp_path):
    directory = tmp_path / "cache" / "key"
    (directory / "files").mkdir(parents=True)
    artifact = directory / "files/artifact.json"
    artifact.write_text('{"result": 1}')
    write_json(
        directory / "manifest.json",
        {"key": "key", "outputs": {"artifact.json": digest(artifact)}},
    )
    assert cache_hit(directory.parent, "key", ["artifact.json"]) is not None
    return directory


@pytest.mark.parametrize("alias", ["entry", "files", "manifest"])
def test_hash_valid_external_cache_alias_is_not_reused(entry, tmp_path, alias):
    source = {
        "entry": entry,
        "files": entry / "files",
        "manifest": entry / "manifest.json",
    }[alias]
    target = tmp_path / f"moved-{alias}"
    source.rename(target)
    source.symlink_to(target, target_is_directory=alias != "manifest")
    assert cache_hit(entry.parent, "key", ["artifact.json"]) is None


def test_worker_output_directory_alias_is_not_publishable(entry, tmp_path):
    output = tmp_path / "worker-files"
    output.symlink_to(entry / "files", target_is_directory=True)
    with pytest.raises(ValueError, match="output directory"):
        hash_outputs(output, ["artifact.json"])


def test_duplicate_manifest_keys_fail_closed(entry):
    original = json.loads((entry / "manifest.json").read_text())
    (entry / "manifest.json").write_text(
        '{"key":"wrong","key":"key","outputs":' + json.dumps(original["outputs"]) + "}"
    )
    assert cache_hit(entry.parent, "key", ["artifact.json"]) is None
