"""Receipt-only stage reuse, selective identities and retained failure evidence."""

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.workflows.stage_ledger import StageLedger, VideoStages, stage_identity


def recipe(**changes):
    value = {
        "provider": "fixture.export",
        "version": "v1.0",
        "config": {"size": 2, "enabled": True},
        "inputs": {"video": "a" * 64},
        "runtime": {"python": "fixture-python-v1"},
        "implementation": {"workflows/export.py": "b" * 64},
    }
    value.update(changes)
    return value


def action(root, name, value="artifact"):
    def work():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return {"path": str(path.relative_to(root)), "value": value}, [path]

    return work


def must_reuse():
    pytest.fail("Verified stage unexpectedly reran")


def test_identity_is_order_independent_and_does_not_mutate_the_recipe():
    source = recipe()
    before = copy.deepcopy(source)
    reordered = dict(reversed(list(source.items())))
    reordered["config"] = dict(reversed(list(source["config"].items())))
    assert stage_identity(source) == stage_identity(reordered)
    assert stage_identity(source) == stage_identity({**source, "dependencies": {}})
    assert source == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "different.export"),
        ("version", "v2.0"),
        ("config", {"size": 3}),
        ("inputs", {"video": "c" * 64}),
        ("runtime", {"python": "different-runtime"}),
        ("implementation", {"workflows/export.py": "d" * 64}),
        ("dependencies", {"observations": "e" * 64}),
    ],
)
def test_every_declared_identity_boundary_invalidates(field, value):
    assert stage_identity(recipe()) != stage_identity(recipe(**{field: value}))


def test_controlled_source_references_bind_content_without_checkout_location(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("implementation")
    expected = digest(source)
    by_hash = recipe(implementation={"export.py": expected})
    by_ref = recipe(
        implementation={"export.py": {"path": "source.py", "sha256": expected}}
    )
    assert stage_identity(by_hash) == stage_identity(by_ref, source_root=tmp_path)
    with pytest.raises(ValueError, match="source_root"):
        stage_identity(by_ref)
    source.write_text("changed implementation")
    with pytest.raises(ValueError, match="changed"):
        stage_identity(by_ref, source_root=tmp_path)


def test_source_reference_cannot_escape_its_explicit_root(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("outside")
    controlled = tmp_path / "controlled"
    controlled.mkdir()
    (controlled / "escape").symlink_to(source)
    for name in ("../source.py", "escape"):
        value = recipe(
            implementation={"export.py": {"path": name, "sha256": digest(source)}}
        )
        with pytest.raises(ValueError, match="escaped"):
            stage_identity(value, source_root=controlled)


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": True},
        {"provider": ""},
        {"version": None},
        {"config": []},
        {"runtime": {}},
        {"inputs": {"video": "not a hash"}},
        {"implementation": {}},
        {"implementation": {"code": "A" * 64}},
        {"dependencies": {"prepare": "not a hash"}},
        {"config": {1: "implicit key coercion"}},
        {"config": {"bad": (1, 2)}},
        {"config": {"bad": Path("not-json")}},
        {"config": {"bad": float("nan")}},
        {"config": {"bad": float("inf")}},
    ],
)
def test_identity_rejects_ambiguous_recipes(changes):
    with pytest.raises(ValueError):
        stage_identity(recipe(**changes))


def test_identity_rejects_cycles_and_missing_required_fields():
    value = recipe()
    value["config"]["cycle"] = value
    with pytest.raises(ValueError, match="cyclic"):
        stage_identity(value)
    value = recipe()
    del value["runtime"]
    with pytest.raises(ValueError, match="missing"):
        stage_identity(value)


def test_legacy_api_history_and_failed_revalidation_stay_compatible(tmp_path):
    ledger = VideoStages(tmp_path, "plan-one")
    expected = ledger.run("export", action(tmp_path, "old/data"))
    assert StageLedger is VideoStages
    assert set(ledger.history[0]) == {"name", "reused", "wall_s"}
    receipt = tmp_path / "stages/export.json"
    before = receipt.read_bytes()
    resumed = VideoStages(tmp_path, "plan-one")
    assert resumed.run("export", must_reuse) == expected
    assert resumed.history == [{"name": "export", "reused": True, "wall_s": 0.0}]
    with pytest.raises(ValueError, match="identity"):
        VideoStages(tmp_path, "plan-two").run("export", must_reuse)
    assert receipt.read_bytes() == before


def test_changed_plan_reuses_explicit_stage_with_hash_and_contract_checks(tmp_path):
    identity = stage_identity(recipe())
    validated = []

    def validate(result, artifacts):
        validated.append((result["value"], artifacts[0]["sha256"]))

    result = VideoStages(tmp_path, "plan-one").run(
        "export", action(tmp_path, "first/data"), validate, stage_identity=identity
    )
    canonical = tmp_path / "stages/export" / f"{identity}.json"
    before = canonical.read_bytes()
    resumed = VideoStages(tmp_path, "plan-two")
    assert (
        resumed.run("export", must_reuse, validate, stage_identity=identity) == result
    )
    assert len(validated) == 2 and validated[0] == validated[1]
    assert canonical.read_bytes() == before
    index = read_json(tmp_path / "stages/export.json")
    assert index["plan_identity"] == "plan-one"
    assert index["receipt"] == str(canonical.relative_to(tmp_path))
    assert index["receipt_sha256"] == digest(canonical)
    assert resumed.history[0]["reused"] is True


def test_only_changed_stage_and_its_dependency_consumers_rerun(tmp_path):
    observe = stage_identity(recipe(provider="observations"))
    reconstruct = stage_identity(
        recipe(provider="reconstruction", dependencies={"observations": observe})
    )
    render = stage_identity(
        recipe(provider="render", dependencies={"reconstruction": reconstruct})
    )
    first = VideoStages(tmp_path, "old-plan")
    for name, identity in (
        ("observations", observe),
        ("reconstruction", reconstruct),
        ("render", render),
    ):
        first.run(name, action(tmp_path, f"old/{name}"), stage_identity=identity)
    old = {path: path.read_bytes() for path in (tmp_path / "stages").glob("*/*.json")}
    changed_reconstruct = stage_identity(
        recipe(
            provider="reconstruction",
            version="v2.0",
            dependencies={"observations": observe},
        )
    )
    changed_render = stage_identity(
        recipe(provider="render", dependencies={"reconstruction": changed_reconstruct})
    )
    upgraded = VideoStages(tmp_path, "new-plan")
    upgraded.run("observations", must_reuse, stage_identity=observe)
    upgraded.run(
        "reconstruction",
        action(tmp_path, "new/reconstruction"),
        stage_identity=changed_reconstruct,
    )
    upgraded.run(
        "render", action(tmp_path, "new/render"), stage_identity=changed_render
    )
    assert [row["reused"] for row in upgraded.history] == [True, False, False]
    assert all(path.read_bytes() == content for path, content in old.items())
    assert (tmp_path / "old/reconstruction").is_file()
    assert read_json(tmp_path / "stages/render.json")["identity"] == changed_render


@pytest.mark.parametrize("fresh", [False, True])
def test_contract_failure_preserves_previous_success_and_index(tmp_path, fresh):
    old = stage_identity(recipe())
    new = stage_identity(recipe(version="v2.0")) if fresh else old
    VideoStages(tmp_path, "old-plan").run(
        "export", action(tmp_path, "first/data"), stage_identity=old
    )
    index = tmp_path / "stages/export.json"
    canonical = tmp_path / "stages/export" / f"{old}.json"
    before = index.read_bytes(), canonical.read_bytes()

    def reject(*args):
        raise ValueError("semantic rejection")

    ledger = VideoStages(tmp_path, "new-plan")
    with pytest.raises(ValueError, match="semantic rejection"):
        ledger.run(
            "export",
            action(tmp_path, "second/data") if fresh else must_reuse,
            reject,
            stage_identity=new,
        )
    assert (index.read_bytes(), canonical.read_bytes()) == before
    failure = read_json(next((tmp_path / "stage-failures/export").glob("*.json")))
    assert ledger.history == [failure]
    assert failure["reused"] is not fresh and failure["stage_identity"] == new


def test_missing_index_is_restored_but_missing_canonical_is_not_silently_recreated(
    tmp_path,
):
    identity = stage_identity(recipe())
    VideoStages(tmp_path, "plan").run(
        "export", action(tmp_path, "data"), stage_identity=identity
    )
    index = tmp_path / "stages/export.json"
    original = index.read_bytes()
    index.unlink()
    VideoStages(tmp_path, "new-plan").run("export", must_reuse, stage_identity=identity)
    assert index.read_bytes() == original
    (tmp_path / "stages/export" / f"{identity}.json").unlink()
    with pytest.raises(ValueError, match="immutable stage receipt is missing"):
        VideoStages(tmp_path, "plan").run("export", must_reuse, stage_identity=identity)
    assert index.read_bytes() == original


@pytest.mark.parametrize("corruption", ["artifact", "index", "canonical"])
def test_reuse_rejects_corrupt_evidence_without_running_action(tmp_path, corruption):
    identity = stage_identity(recipe())
    VideoStages(tmp_path, "plan").run(
        "export", action(tmp_path, "data"), stage_identity=identity
    )
    if corruption == "artifact":
        (tmp_path / "data").write_text("tampered")
    else:
        path = (
            tmp_path
            / "stages"
            / ("export.json" if corruption == "index" else f"export/{identity}.json")
        )
        record = read_json(path)
        record["result"]["value"] = "tampered"
        write_json(path, record)
    with pytest.raises(ValueError):
        VideoStages(tmp_path, "plan").run("export", must_reuse, stage_identity=identity)


def test_failed_index_publication_keeps_canonical_reusable_and_old_index_intact(
    tmp_path, monkeypatch
):
    from simready.workflows import stage_ledger

    old, new = stage_identity(recipe()), stage_identity(recipe(version="v2.0"))
    VideoStages(tmp_path, "plan-one").run(
        "export", action(tmp_path, "old/data"), stage_identity=old
    )
    index = tmp_path / "stages/export.json"
    before = index.read_bytes()
    original = stage_ledger.write_json

    def failed_index(path, value):
        if path == index:
            raise OSError("index disk failure")
        original(path, value)

    with monkeypatch.context() as context:
        context.setattr(stage_ledger, "write_json", failed_index)
        with pytest.raises(OSError, match="index disk failure"):
            VideoStages(tmp_path, "plan-two").run(
                "export", action(tmp_path, "new/data"), stage_identity=new
            )
    assert index.read_bytes() == before
    assert (tmp_path / "stages/export" / f"{new}.json").is_file()
    VideoStages(tmp_path, "plan-two").run("export", must_reuse, stage_identity=new)
    assert read_json(index)["identity"] == new


def test_explicit_upgrade_archives_the_legacy_success_before_switching_index(tmp_path):
    VideoStages(tmp_path, "legacy-plan").run("export", action(tmp_path, "old/data"))
    old = read_json(tmp_path / "stages/export.json")
    identity = stage_identity(recipe())
    VideoStages(tmp_path, "new-plan").run(
        "export", action(tmp_path, "new/data"), stage_identity=identity
    )
    archives = list((tmp_path / "stages/export").glob("legacy-*.json"))
    assert len(archives) == 1 and read_json(archives[0]) == old


@pytest.mark.parametrize(
    "error,status",
    [
        (ProcessCancelledError(0), "CANCELLED"),
        (ProcessTimeoutError(1, 1), "TIMEOUT"),
        (KeyboardInterrupt(), "INTERRUPTED"),
    ],
)
def test_failure_accounting_preserves_the_original_exception(tmp_path, error, status):
    def fail():
        raise error

    ledger = VideoStages(tmp_path, "plan")
    with pytest.raises(type(error)) as raised:
        ledger.run("export", fail)
    assert raised.value is error
    assert ledger.history[0]["status"] == status
    assert not (tmp_path / "stages/export.json").exists()


def test_failure_receipt_io_error_does_not_mask_cancellation(tmp_path, monkeypatch):
    from simready.workflows import stage_ledger

    original = ProcessCancelledError(0)

    def fail():
        raise original

    def disk_error(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(stage_ledger, "write_json", disk_error)
    ledger = VideoStages(tmp_path, "plan")
    with pytest.raises(ProcessCancelledError) as raised:
        ledger.run("export", fail)
    assert raised.value is original
    assert ledger.history[0]["receipt_error"] == "OSError"


def test_immutable_publication_cannot_replace_a_concurrent_success(tmp_path):
    identity = stage_identity(recipe())
    barrier = Barrier(2)

    def run(index):
        def work():
            result = action(tmp_path, f"attempt-{index}/data", str(index))()
            barrier.wait(timeout=3)
            return result

        try:
            return VideoStages(tmp_path, "plan").run(
                "export", work, stage_identity=identity
            )
        except FileExistsError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    canonical = read_json(tmp_path / "stages/export" / f"{identity}.json")
    assert canonical["result"] == winner
    assert read_json(tmp_path / "stages/export.json")["result"] == winner


def test_receipt_symlinks_and_unsafe_stage_names_are_rejected(tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    root = tmp_path / "run"
    root.mkdir()
    (root / "stages").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        VideoStages(root, "plan").run("export", must_reuse)
    assert not list(outside.iterdir())
    with pytest.raises(ValueError, match="plain"):
        VideoStages(root, "plan").run("../outside", must_reuse)
