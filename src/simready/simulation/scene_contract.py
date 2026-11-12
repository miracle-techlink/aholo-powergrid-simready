"""Resolve portable scene contracts before starting Kit or CUDA."""

from pathlib import Path

from simready.core.artifacts import read_json


def mission_config(path):
    path = Path(path).resolve()
    config = read_json(path)
    if config.get("intent") != "stop_simulated_load":
        raise ValueError("Only the reviewed stop_simulated_load task is supported")
    for key in (
        "environment_manifest",
        "scene_config",
        "rig_report",
        "locomotion_policy",
    ):
        value = (path.parent / config[key]).resolve()
        if not value.is_file():
            raise FileNotFoundError(f"Missing {key}: {value}")
        config[key] = str(value)
    return config
