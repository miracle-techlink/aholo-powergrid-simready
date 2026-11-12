"""Deployment adapters must keep virtualenv interpreter identity."""

from types import SimpleNamespace

from examples.deployment_profiling.prepare_speed_benchmark import prepare
from simready.core.artifacts import read_json, write_json


def test_virtualenv_executable_symlink_is_not_resolved(tmp_path, monkeypatch):
    deployment = tmp_path / "deployment"
    for name in ["scripts", "simready_bridge", "configs", "artifacts", "reports"]:
        (deployment / name).mkdir(parents=True)
    (deployment / "scripts/render_panel_stop_mission.py").write_text(
        "def run(a, data):\n    selected=list(map(int,a.frames.split(','))) if a.frames else list(range(len(data['steps'])))\n"
        "    try:\n        for ordinal,i in enumerate(selected):\n            pass\n    finally:\n        pass\n"
    )
    (deployment / "simready_bridge/__init__.py").write_text("")
    mission = tmp_path / "mission"
    mission.mkdir()
    write_json(mission / "result.json", {"passed": True})
    write_json(mission / "scene_preflight.json", {"dependencies": []})
    for name in ["scene.usda", "measured_replay.npz", "sensors.jsonl"]:
        (mission / name).write_text("test fixture")
    plan = tmp_path / "plan.json"
    write_json(plan, {"tasks": [{"stage": "render", "inputs": {}}]})
    real = tmp_path / "system-python"
    real.write_text("")
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(real)
    monkeypatch.setattr(
        "examples.deployment_profiling.prepare_speed_benchmark.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="synthetic GPU, driver"),
    )
    protocol = read_json(prepare(deployment, mission, plan, python, tmp_path / "bench"))
    assert protocol["baseline"]["argv"][0] == str(python.absolute())
    assert protocol["baseline"]["argv"][0] != str(python.resolve())
    assert protocol["candidates"][0]["id"] == "disable_background_viewport_after16"
