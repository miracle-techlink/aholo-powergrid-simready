"""Trusted task templates and bounded parameter variants for agent selection."""

import copy
import math
import re
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.types import JsonObject, PathLike


def validate_parameters(values, schema):
    if not isinstance(values, dict) or set(values) != set(schema):
        raise ValueError("Plugin parameters must exactly match the declared fields")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for name, spec in schema.items():
        value = values[name]
        expected = types.get(spec["type"])
        if (
            expected is None
            or not isinstance(value, expected)
            or (isinstance(value, bool) and spec["type"] != "boolean")
        ):
            raise ValueError(f"Invalid plugin parameter type: {name}")
        if spec["type"] in ("number", "integer"):
            if (
                not math.isfinite(value)
                or value < spec.get("minimum", -math.inf)
                or value > spec.get("maximum", math.inf)
            ):
                raise ValueError(f"Plugin parameter out of bounds: {name}")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"Unknown plugin parameter choice: {name}")


class RepairPluginRegistry:
    """Validate executable plugins, propose repairs and bind trusted task plans."""

    def __init__(self, path: PathLike) -> None:
        self.path = Path(path).resolve()
        value = read_json(self.path)
        if (
            value.get("schema_version") != 1
            or value.get("execution_enabled") is not True
        ):
            raise ValueError("Executable plugin registry must be explicitly enabled")
        self.plugins = {}
        self.input_files = []
        for plugin in value["plugins"]:
            key = plugin["plugin_id"]
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", key) or key in self.plugins:
                raise ValueError("Invalid or duplicate plugin ID")
            for name in ("version", "description"):
                if not isinstance(plugin.get(name), str) or not plugin[name]:
                    raise ValueError(f"Plugin requires {name}")
            for name in ("handles", "profiles", "families", "variants"):
                if not isinstance(plugin.get(name), list) or not plugin[name]:
                    raise ValueError(f"Plugin requires nonempty {name}")
            if type(plugin.get("priority")) is not int or plugin["priority"] < 0:
                raise ValueError("Plugin priority must be nonnegative")
            for variant in plugin["variants"]:
                validate_parameters(variant["parameters"], plugin["parameter_schema"])
            tasks = plugin["tasks"]
            ids = [task["id"] for task in tasks]
            if len(set(ids)) != len(ids) or plugin["result_task"] not in ids:
                raise ValueError("Plugin result task must name a unique task")
            final = next(task for task in tasks if task["id"] == plugin["result_task"])
            if "candidate.json" not in final["outputs"]:
                raise ValueError("Plugin result task must declare candidate.json")
            implementation = {
                "tasks": copy.deepcopy(tasks),
                "parameter_schema": plugin["parameter_schema"],
            }
            for task in implementation["tasks"]:
                self.input_files += [
                    {
                        "path": str((self.path.parent / filename).resolve()),
                        "sha256": digest(self.path.parent / filename),
                    }
                    for filename in task.get("inputs", {}).values()
                ]
                task["inputs"] = {
                    name: digest(self.path.parent / filename)
                    for name, filename in task.get("inputs", {}).items()
                }
            plugin["version"] += "+" + object_digest(implementation)[:16]
            self.plugins[key] = plugin

    @property
    def versions(self) -> dict[str, str]:
        return {key: value["version"] for key, value in self.plugins.items()}

    def propose_repairs(
        self,
        protocol: JsonObject,
        family: str,
        defects: list[JsonObject],
        skills: list[JsonObject],
        failed_actions: set[str],
    ) -> list[JsonObject]:
        result = {}
        for plugin in self.plugins.values():
            if (
                protocol["profile"] not in plugin["profiles"]
                or family not in plugin["families"]
            ):
                continue
            if plugin.get("fixture") and (
                protocol["profile"] != "observer_calibration"
                or any(v["reference_kind"] != "synthetic_gt" for v in protocol["views"])
            ):
                continue
            relevant = [
                d
                for d in defects
                if d["repairable_now"] and d["kind"] in plugin["handles"]
            ]
            if not relevant:
                continue
            variants = [dict(v, skill_id=None) for v in plugin["variants"]]
            variants += [
                dict(parameters=s["parameters"], skill_id=s["skill_id"])
                for s in skills
                if s["plugin_id"] == plugin["plugin_id"]
                and s["plugin_version"] == plugin["version"]
            ]
            for variant in variants:
                validate_parameters(variant["parameters"], plugin["parameter_schema"])
                body = {
                    "plugin_id": plugin["plugin_id"],
                    "plugin_version": plugin["version"],
                    "parameters": variant["parameters"],
                    "asset_ids": sorted({a for d in relevant for a in d["asset_ids"]}),
                }
                key = object_digest(body)
                if key in failed_actions:
                    continue
                result[key] = dict(
                    body,
                    proposal_id=key,
                    action_key=key,
                    skill_id=variant["skill_id"],
                    description=plugin["description"],
                    priority=plugin["priority"],
                    defect_kinds=sorted({d["kind"] for d in relevant}),
                )
        return list(result.values())

    def build_task_plan(
        self,
        proposal: JsonObject,
        *,
        scene_id: str,
        candidate: PathLike,
        protocol: PathLike,
        directory: PathLike,
        input_files: list[JsonObject],
    ) -> tuple[JsonObject, str]:
        plugin = self.plugins[proposal["plugin_id"]]
        if plugin["version"] != proposal["plugin_version"]:
            raise ValueError("Plugin version changed")
        validate_parameters(proposal["parameters"], plugin["parameter_schema"])
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        parameters = directory / "parameters.json"
        write_json(
            parameters,
            {"parameters": proposal["parameters"], "asset_ids": proposal["asset_ids"]},
        )
        tasks = copy.deepcopy(plugin["tasks"])
        for task in tasks:
            task["scene_id"] = scene_id
            task["cache"] = (
                False  # Repair side effects and final evidence are never implicitly replayed.
            )
            task["inputs"] = {
                name: str((self.path.parent / path).resolve())
                for name, path in task.get("inputs", {}).items()
            }
            if {"candidate", "protocol", "parameters"} & task["inputs"].keys() or any(
                key.startswith("evidence_") for key in task["inputs"]
            ):
                raise ValueError("Plugin cannot override reserved input bindings")
            task["inputs"].update(
                candidate=str(Path(candidate).resolve()),
                protocol=str(Path(protocol).resolve()),
                parameters=str(parameters),
            )
            for i, ref in enumerate(input_files):
                if digest(ref["path"]) != ref["sha256"]:
                    raise ValueError("Observation input changed before plugin dispatch")
                task["inputs"][f"evidence_{i}"] = ref["path"]
        return {"schema_version": 1, "tasks": tasks}, plugin["result_task"]
