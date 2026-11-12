"""Export reviewed scene dependencies into a relocatable, hash-checked bundle.

This packages existing reconstruction/repair outputs; it does not invent assets.
Run with a Python environment containing OpenUSD (pxr).
"""

import argparse
import os
import shutil
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json


class BundleExporter:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.copied = {}
        self.sources = {}

    def copy(self, source):
        from pxr import Sdf, UsdUtils

        source = Path(source).resolve()
        if source in self.copied:
            return self.copied[source]
        if not source.is_file():
            raise FileNotFoundError(source)
        sha = digest(source)
        target = self.output / "assets" / sha[:20] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        self.copied[source] = target
        self.sources[str(target.relative_to(self.output))] = sha
        if source.suffix.lower() in (".usd", ".usda", ".usdc"):
            original = Sdf.Layer.FindOrOpen(str(source))
            if original is None:
                raise ValueError(f"Cannot open USD: {source}")
            layer = Sdf.Layer.CreateNew(str(target))
            layer.TransferContent(original)

            def remap(value):
                if not value:
                    return value
                # Built-in MDL modules resolve through Kit's search path.
                if value.endswith(".mdl") and "/" not in value:
                    return value
                resolved = Sdf.ComputeAssetPathRelativeToLayer(original, value)
                dependency = self.copy(resolved)
                return os.path.relpath(dependency, target.parent)

            UsdUtils.ModifyAssetPaths(layer, remap)
            layer.Save()
        else:
            shutil.copy2(source, target)
        return target

    def relative(self, path):
        return str(Path(path).relative_to(self.output))

    def export(self, spec_path):
        spec_path = Path(spec_path).resolve()
        spec = read_json(spec_path)

        def source(key):
            return (spec_path.parent / spec[key]).resolve()

        scene = spec["scene"]
        if scene not in ("starbucks", "substation"):
            raise ValueError("Unknown scene")
        video = self.copy(source("source_video"))
        bundle = {
            "schema_version": 1,
            "scene": scene,
            "source_video": self.relative(video),
            "source_video_sha256": digest(video),
            "provenance": spec.get("provenance", "reviewed local assets"),
            "new_reconstruction": False,
        }
        if scene == "starbucks":
            for key in ("scene_usd", "robot_usd", "color_layer"):
                if spec.get(key):
                    bundle[key] = self.relative(self.copy(source(key)))
        else:
            config_path = source("mission_config")
            cfg = read_json(config_path)

            def mission_path(key):
                return (config_path.parent / cfg[key]).resolve()

            manifest = read_json(mission_path("environment_manifest"))
            # Only executable control specifications belong to the runtime contract.
            write_json(
                self.output / "controls.json",
                {
                    "buttons": manifest["buttons"],
                    "rotaries": manifest.get("rotaries", []),
                },
            )
            scene_path = mission_path("scene_config")
            scene_config = read_json(scene_path)
            s = scene_config["scene"]
            for key in (
                "usd_path",
                "visual_source_path",
                "visual_reference_path",
                "collision_derived_from_path",
            ):
                if key in s:
                    s[key] = self.relative(self.copy(scene_path.parent / s[key]))
            # Path rewriting changes USD bytes; maintain fresh bundle hashes and original source hashes separately.
            for key, hashkey in (
                ("usd_path", "usd_sha256"),
                ("visual_source_path", "visual_source_sha256"),
                ("visual_reference_path", "visual_reference_sha256"),
            ):
                if key in s:
                    s[hashkey] = digest(self.output / s[key])
            write_json(self.output / "scene.json", scene_config)
            rig_path = mission_path("rig_report")
            rig = read_json(rig_path)
            rig_usd = self.copy(rig_path.parent / rig["rig_path"])
            rig = {
                key: rig[key]
                for key in ("schema_version", "units", "up_axis", "groups", "joints")
            }
            rig.update(rig_path=self.relative(rig_usd), rig_sha256=digest(rig_usd))
            write_json(self.output / "rig.json", rig)
            cfg.update(
                environment_manifest="controls.json",
                scene_config="scene.json",
                rig_report="rig.json",
                locomotion_policy=self.relative(
                    self.copy(mission_path("locomotion_policy"))
                ),
            )
            write_json(self.output / "mission.json", cfg)
            bundle["mission_config"] = "mission.json"
        bundle["files"] = [
            {
                "path": self.relative(p),
                "sha256": digest(p),
                "bytes": p.stat().st_size,
                **(
                    {"source_sha256": self.sources[self.relative(p)]}
                    if self.relative(p) in self.sources
                    else {}
                ),
            }
            for p in sorted(self.output.rglob("*"))
            if p.is_file()
        ]
        write_json(self.output / "bundle.json", bundle)
        return bundle


def copy_layer_with_resolved_paths(source, destination):
    """Clone an authored layer without changing what its relative references resolve to."""
    from pxr import Sdf, UsdUtils

    original = Sdf.Layer.FindOrOpen(str(source))
    if original is None:
        raise ValueError(f"Cannot open USD: {source}")
    layer = Sdf.Layer.CreateNew(str(destination))
    layer.TransferContent(original)
    UsdUtils.ModifyAssetPaths(
        layer,
        lambda value: (
            Sdf.ComputeAssetPathRelativeToLayer(original, value) if value else value
        ),
    )
    layer.Save()
    return layer


def verify_bundle(path):
    root = Path(path).resolve()
    bundle = read_json(root / "bundle.json")
    if bundle.get("schema_version") != 1:
        raise ValueError("Unsupported bundle version")
    listed = set()
    for record in bundle["files"]:
        value = (root / record["path"]).resolve()
        if not value.is_relative_to(root) or record["path"] in listed:
            raise ValueError("Invalid or duplicate bundle path")
        listed.add(record["path"])
        if (
            not value.is_file()
            or value.stat().st_size != record["bytes"]
            or digest(value) != record["sha256"]
        ):
            raise ValueError(f"Missing or changed bundle file: {record['path']}")
    required = [bundle["source_video"]]
    if bundle["scene"] == "starbucks":
        required += [bundle["scene_usd"], bundle["robot_usd"]]
        if bundle.get("color_layer"):
            required.append(bundle["color_layer"])
    elif bundle["scene"] == "substation":
        required.append(bundle["mission_config"])
    else:
        raise ValueError("Unknown bundle scene")
    if (
        not all(p in listed for p in required)
        or digest(root / bundle["source_video"]) != bundle["source_video_sha256"]
    ):
        raise ValueError("Bundle entry point is not bound to its file receipt")
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    BundleExporter(args.out).export(args.spec)
    verify_bundle(args.out)
    print(args.out / "bundle.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
