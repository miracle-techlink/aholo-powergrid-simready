import copy
import json
from pathlib import Path

import pytest

from simready.core.artifacts import object_digest, write_json
from simready.execution.tasks.environment import RESERVED, freeze_environment
from simready.execution.tasks.schema import validate
from tests.fixtures.task_execution import INVENTORY, execute, task

SETTING = "SIMREADY_TEST_SETTING"
UNDECLARED = "SIMREADY_TEST_UNDECLARED"
SECRET = "SIMREADY_TEST_CREDENTIAL"


def environment_task():
    item = task()
    item["environment"] = {"inherit": [SETTING]}
    item["argv"] = [
        "{python}",
        "-c",
        "import json,os,pathlib; "
        f"pathlib.Path('artifact.json').write_text(json.dumps({{name:os.environ.get(name) for name in {[SETTING, UNDECLARED]!r} }}))",
    ]
    return item


def output(result, name="one"):
    return json.loads(
        (Path(result["tasks"][name]["artifact_dir"]) / "artifact.json").read_text()
    )


def test_declared_host_changes_miss_cache_and_undeclared_values_never_reach_child(
    tmp_path, monkeypatch
):
    item = environment_task()
    monkeypatch.setenv(SETTING, "first")
    monkeypatch.setenv(UNDECLARED, "hidden")
    first = execute(tmp_path, [item])
    assert first["status"] == "SUCCESS"
    assert output(first) == {SETTING: "first", UNDECLARED: None}
    assert first["tasks"]["one"]["cache_enabled"] is True
    monkeypatch.setenv(UNDECLARED, "different hidden value")
    cached = execute(tmp_path, [item], "cached")
    assert cached["cache_hits"] == 1
    assert cached["tasks"]["one"]["key"] == first["tasks"]["one"]["key"]
    monkeypatch.setenv(SETTING, "second")
    changed = execute(tmp_path, [item], "changed")
    assert changed["cache_hits"] == 0
    assert output(changed)[SETTING] == "second"
    assert changed["tasks"]["one"]["key"] != first["tasks"]["one"]["key"]


def test_missing_inheritance_and_present_empty_string_are_distinct(
    tmp_path, monkeypatch
):
    item = environment_task()
    monkeypatch.delenv(SETTING, raising=False)
    missing = execute(tmp_path, [item])
    assert output(missing)[SETTING] is None
    assert missing["tasks"]["one"]["environment"]["missing_inherited_names"] == [
        SETTING
    ]
    monkeypatch.setenv(SETTING, "")
    empty = execute(tmp_path, [item], "empty")
    assert empty["cache_hits"] == 0
    assert output(empty)[SETTING] == ""
    assert empty["tasks"]["one"]["environment"]["missing_inherited_names"] == []


def test_literal_overrides_determine_effective_identity_and_receipts_are_redacted(
    tmp_path, monkeypatch
):
    item = environment_task()
    item["env"] = {SETTING: "literal-nonsecret"}
    monkeypatch.setenv(SETTING, "host-first")
    first = execute(tmp_path, [item])
    assert output(first)[SETTING] == "literal-nonsecret"
    assert first["tasks"]["one"]["environment"]["nonsecret_sha256"] == object_digest(
        {SETTING: "literal-nonsecret"}
    )
    monkeypatch.setenv(SETTING, "host-second")
    assert execute(tmp_path, [item], "cached")["cache_hits"] == 1
    receipt = json.loads((tmp_path / "run/plan.json").read_text())
    assert receipt["tasks"][0]["env"][SETTING] == "<redacted>"
    assert item["env"][SETTING] == "literal-nonsecret"
    with pytest.raises(ValueError, match="Redacted plan"):
        validate(receipt, INVENTORY, tmp_path)
    item["env"][SETTING] = "changed-literal"
    changed = execute(tmp_path, [item], "changed")
    assert changed["cache_hits"] == 0 and output(changed)[SETTING] == "changed-literal"


@pytest.mark.parametrize("legacy", [False, True])
def test_graph_freezes_environment_before_any_dispatch_including_dependent_tasks(
    tmp_path, monkeypatch, legacy
):
    from simready.execution.tasks import graph as task_graph

    first = environment_task()
    second = copy.deepcopy(first)
    second.update(id="later", deps=["one"])
    if legacy:
        del first["environment"], second["environment"]
    monkeypatch.setenv(SETTING, "frozen")
    original = task_graph.execute_task

    def mutate_host_before_worker(*args, **kwargs):
        monkeypatch.setenv(SETTING, "mutated after graph startup")
        return original(*args, **kwargs)

    monkeypatch.setattr(task_graph, "execute_task", mutate_host_before_worker)
    result = execute(tmp_path, [first, second])
    assert result["status"] == "SUCCESS"
    assert output(result)[SETTING] == output(result, "later")[SETTING] == "frozen"


def test_legacy_v1_runs_with_full_inheritance_but_never_reuses_or_publishes_cache(
    tmp_path, monkeypatch
):
    item = environment_task()
    del item["environment"]
    monkeypatch.setenv(SETTING, "legacy-first")
    monkeypatch.setenv(UNDECLARED, "legacy-extra")
    first = execute(tmp_path, [item])
    record = first["tasks"]["one"]
    assert output(first) == {SETTING: "legacy-first", UNDECLARED: "legacy-extra"}
    assert record["cache_requested"] is True and record["cache_enabled"] is False
    assert record["cache_disabled_reason"] == "legacy_environment_untracked"
    assert record["environment"]["nonsecret_sha256"] is None
    assert not list((tmp_path / "cache").iterdir())
    # Even a matching, valid entry from elsewhere must not bypass this policy.
    entry = tmp_path / "cache" / record["key"]
    (entry / "files").mkdir(parents=True)
    (entry / "files/artifact.json").write_bytes(
        (Path(record["artifact_dir"]) / "artifact.json").read_bytes()
    )
    write_json(
        entry / "manifest.json", {"key": record["key"], "outputs": record["outputs"]}
    )
    monkeypatch.setenv(SETTING, "legacy-second")
    second = execute(tmp_path, [item], "second")
    assert second["cache_hits"] == 0
    assert output(second)[SETTING] == "legacy-second"
    assert second["tasks"]["one"]["key"] == record["key"]
    assert len(list((tmp_path / "cache").iterdir())) == 1


def test_secret_is_delivered_without_value_or_digest_in_metadata_and_disables_cache(
    tmp_path, monkeypatch
):
    item = task()
    item["environment"] = {"inherit": [], "secrets": [SECRET]}
    item["argv"] = [
        "{python}",
        "-c",
        f"import os,pathlib; assert os.environ[{SECRET!r}]; pathlib.Path('artifact.json').write_text('true')",
    ]
    keys = []
    for index, secret in enumerate(("credential-alpha-123", "credential-beta-456")):
        monkeypatch.setenv(SECRET, secret)
        result = execute(tmp_path, [item], f"run{index}")
        record = result["tasks"]["one"]
        assert result["status"] == "SUCCESS" and result["cache_hits"] == 0
        assert record["cache_disabled_reason"] == "secret_environment"
        assert record["cache_enabled"] is False
        assert record["environment"]["secret_names"] == [SECRET]
        assert record["environment"]["nonsecret_sha256"] == object_digest({})
        keys.append(record["key"])
        for path in (tmp_path / f"run{index}").rglob("*"):
            if path.is_file():
                text = path.read_text()
                assert secret not in text
                assert object_digest({SECRET: secret}) not in text
        assert not list((tmp_path / "cache").iterdir())
    assert keys[0] == keys[1], "Credential values must not affect a persisted digest"


def test_missing_required_secret_fails_before_creating_outputs(tmp_path, monkeypatch):
    item = task()
    item["environment"] = {"secrets": [SECRET]}
    monkeypatch.delenv(SECRET, raising=False)
    with pytest.raises(ValueError, match="Missing required environment secret"):
        execute(tmp_path, [item])
    assert not (tmp_path / "run").exists() and not (tmp_path / "cache").exists()


def test_legacy_literal_environment_values_are_neither_persisted_nor_hashed(tmp_path):
    item = task()
    del item["environment"]
    item["argv"] = [
        "{python}",
        "-c",
        f"import os,pathlib; assert os.environ[{SECRET!r}]; pathlib.Path('artifact.json').write_text('true')",
    ]
    keys = []
    for index, secret in enumerate(("legacy-credential-123", "legacy-credential-456")):
        item["env"] = {SECRET: secret}
        result = execute(tmp_path, [item], f"run{index}")
        assert result["status"] == "SUCCESS" and result["cache_hits"] == 0
        keys.append(result["tasks"]["one"]["key"])
        for path in (tmp_path / f"run{index}").rglob("*.json"):
            assert secret not in path.read_text()
    assert keys[0] == keys[1]
    assert not list((tmp_path / "cache").iterdir())


def test_frozen_values_are_private_immutable_and_ignore_later_host_mutations():
    host = {SECRET: "credential-value"}
    snapshot = freeze_environment({"environment": {"secrets": [SECRET]}}, host)
    host[SECRET] = "changed"
    assert snapshot.values[SECRET] == "credential-value"
    assert "credential-value" not in repr(snapshot)
    with pytest.raises(TypeError):
        snapshot.values[SECRET] = "cannot change"


def test_explicit_cache_false_is_reported(tmp_path):
    item = task()
    item["cache"] = False
    result = execute(tmp_path, [item])
    assert result["status"] == "SUCCESS"
    assert result["tasks"]["one"]["cache_disabled_reason"] == "cache_disabled_by_plan"
    assert result["tasks"]["one"]["cache_requested"] is False
    assert not list((tmp_path / "cache").iterdir())


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": None},
        {"environment": []},
        {"environment": {"unknown": []}},
        {"environment": {"inherit": "PATH"}},
        {"environment": {"inherit": ["DUP", "DUP"]}},
        {"environment": {"inherit": ["BAD=NAME"]}},
        {"environment": {"secrets": [2]}},
        {"environment": {"secrets": ["KEY", "KEY"]}},
        {"environment": {"inherit": ["KEY"], "secrets": ["KEY"]}},
        {"env": {"KEY": "x"}, "environment": {"secrets": ["KEY"]}},
        {"env": []},
        {"env": {"KEY": None}},
        {"env": {"KEY": "has\x00nul"}},
        {"env": {"BAD\x00NAME": "x"}},
    ],
)
def test_malformed_environment_contract_fails_before_work(tmp_path, changes):
    item = task()
    item.update(changes)
    with pytest.raises(ValueError):
        execute(tmp_path, [item])
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("name", sorted(RESERVED))
@pytest.mark.parametrize("location", ["env", "inherit", "secrets"])
def test_scheduler_bindings_cannot_be_inherited_or_overridden(tmp_path, name, location):
    item = task()
    if location == "env":
        item["env"] = {name: "override"}
    else:
        item["environment"] = {location: [name]}
    with pytest.raises(ValueError, match="scheduler"):
        execute(tmp_path, [item])
    assert not (tmp_path / "run").exists()
