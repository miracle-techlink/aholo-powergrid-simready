"""Read composed USD independently and compare physical assets to input evidence.

This CPU inspection verifies assignment and authored values, not material
prediction accuracy or simulated/real dynamics. It intentionally does not import
the physical authoring implementation. Run the module in a fresh process for an
independent worker, supplying the original scene specification as the expectation.
"""

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, read_json, write_json
from simready.evaluation.inspection.comparison import _json_detail, _record, _value
from simready.evaluation.inspection.expectations import (
    _expected_physics,
    _objects,
    _paths,
    _physical_contract,
)
from simready.evaluation.inspection.physical_checks import _body, _geometry, _provenance


def inspect_physical_assets(
    directory: str | Path, *, scene_spec: str | Path | None = None
) -> dict[str, Any]:
    """Compare each authored object's USD values against a separate input spec.

    Missing reference specification returns NOT_EVALUATED. Malformed manifests,
    missing files and unavailable USD bindings raise; value/assignment mismatches
    produce a FAIL report. No dynamics or real-world accuracy is inferred.
    """
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    root = Path(directory).resolve()
    catalog_path = root / "asset_catalog.json"
    scene_path = root / "scene.usda"
    catalog = read_json(catalog_path)
    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "physical_asset_inspection",
        "scope": "independent composed USD assignment and value readback",
        "passed": False,
        "status": "NOT_EVALUATED",
        "checks": [],
        "assets": [],
        "inputs": {
            "scene": {"path": str(scene_path), "sha256": digest(scene_path)},
            "catalog": {"path": str(catalog_path), "sha256": digest(catalog_path)},
        },
        "semantic_accuracy": {
            "status": "NOT_EVALUATED",
            "reason": "No independent semantic ground truth is supplied.",
        },
        "physical_response": {
            "status": "NOT_EVALUATED",
            "reason": "No dynamics rollout or convergence probe is run by this inspector.",
        },
        "real_world_calibration": {
            "status": "NOT_EVALUATED",
            "reason": "USD assignment is not evidence of real-world parameter accuracy.",
        },
        "release_authorized": False,
    }
    if scene_spec is None:
        report["reason"] = (
            "Original scene specification is required; candidate catalog cannot validate itself."
        )
        return report
    spec_path = Path(scene_spec).resolve()
    spec = read_json(spec_path)
    report["inputs"]["scene_spec"] = {
        "path": str(spec_path),
        "sha256": digest(spec_path),
    }
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise ValueError("Expected scene specification schema version 1")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Expected asset catalog schema version 1")
    objects = _objects(spec, "scene_spec")
    catalog_objects = _objects(catalog, "asset_catalog")
    checks = report["checks"]
    _record(checks, "nonempty_assets", bool(objects), count=len(objects))
    _value(checks, "asset_ids", sorted(catalog_objects), sorted(objects))
    stage = Usd.Stage.Open(str(scene_path))
    if stage is None:
        raise ValueError("Cannot open composed scene USD")
    _record(
        checks,
        "composition",
        not stage.GetCompositionErrors(),
        errors=[str(x) for x in stage.GetCompositionErrors()],
    )
    _value(checks, "meters_per_unit", UsdGeom.GetStageMetersPerUnit(stage), 1.0)
    _value(checks, "up_axis", UsdGeom.GetStageUpAxis(stage), "Z")
    dependencies = []
    for layer in stage.GetUsedLayers():
        if layer.anonymous:
            continue
        path = Path(layer.realPath).resolve()
        local = path.is_relative_to(root) and path.is_file()
        _record(checks, "dependency." + layer.identifier, local, path=str(path))
        if local:
            dependencies.append(
                {"path": str(path.relative_to(root)), "sha256": digest(path)}
            )
    report["dependencies"] = sorted(dependencies, key=lambda x: x["path"])
    container = stage.GetPrimAtPath("/World/Objects")
    actual_ids = (
        sorted(
            child.GetName()
            for child in container.GetChildren()
            if not child.IsA(UsdShade.Material)
        )
        if container
        else []
    )
    _value(checks, "usd_asset_ids", actual_ids, sorted(objects))
    assignments: dict[str, Any] = {}
    material_paths = set()
    for key, obj in objects.items():
        start = len(checks)
        catalog_obj = catalog_objects.get(key, {})
        expected = _expected_physics(obj, spec)
        _physical_contract(checks, obj, expected, spec)
        paths = _paths(obj)
        usd_root = paths["root"]
        _value(
            checks,
            key + ".category",
            catalog_obj.get("category"),
            obj.get("category", obj["type"]),
        )
        _value(checks, key + ".geometry_type", catalog_obj.get("type"), obj["type"])
        physics = catalog_obj.get("physics", {})
        for field, target in expected.items():
            _value(checks, key + ".catalog." + field, physics.get(field), target)
        binding = catalog_obj.get("physics_binding", {})
        for field in ("material_prim", "collision_prims", "rigid_body_prims"):
            actual, target = binding.get(field), paths[field]
            if isinstance(actual, list):
                actual, target = sorted(actual), sorted(target)
            _value(checks, key + ".binding." + field, actual, target)
        _provenance(stage, checks, obj, catalog_obj, usd_root, spec)
        root_prim = stage.GetPrimAtPath(usd_root)
        for metadata, expected_metadata in (
            ("object_id", key),
            ("semantic_category", obj.get("category", obj["type"])),
            ("geometry_type", obj["type"]),
        ):
            actual_metadata = (
                root_prim.GetCustomDataByKey("simready:" + metadata)
                if root_prim
                else None
            )
            _value(
                checks,
                key + ".metadata." + metadata,
                actual_metadata,
                expected_metadata,
            )
        if root_prim:
            _value(
                checks,
                key + ".visibility",
                UsdGeom.Imageable(root_prim).ComputeVisibility(),
                "inherited" if obj.get("visible", True) else "invisible",
            )
        observed_colliders = (
            sorted(
                str(p.GetPath())
                for p in Usd.PrimRange(root_prim)
                if p.HasAPI(UsdPhysics.CollisionAPI)
            )
            if root_prim
            else []
        )
        _value(
            checks,
            key + ".collision_inventory",
            observed_colliders,
            sorted(paths["collision_prims"]),
        )
        mapping = []
        for path in paths["collision_prims"]:
            prim = stage.GetPrimAtPath(path)
            valid = bool(prim) and prim.HasAPI(UsdPhysics.CollisionAPI)
            _record(checks, key + ".collision." + path, valid, prim=path)
            if not valid:
                continue
            _value(
                checks,
                key + ".collision_enabled." + path,
                UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get(),
                True,
            )
            material, relationship = UsdShade.MaterialBindingAPI(
                prim
            ).ComputeBoundMaterial(materialPurpose="physics")
            material_path = str(material.GetPath()) if material else None
            _value(
                checks,
                key + ".material_binding." + path,
                material_path,
                paths["material_prim"],
            )
            actual_contact = {}
            valid_material = bool(material) and material.GetPrim().HasAPI(
                UsdPhysics.MaterialAPI
            )
            _record(checks, key + ".physics_material." + path, valid_material)
            if valid_material:
                api = UsdPhysics.MaterialAPI(material.GetPrim())
                for field, getter in (
                    ("static_friction", "GetStaticFrictionAttr"),
                    ("dynamic_friction", "GetDynamicFrictionAttr"),
                    ("restitution", "GetRestitutionAttr"),
                ):
                    actual_contact[field] = getattr(api, getter)().Get()
                    _value(
                        checks,
                        key + ".material." + path + "." + field,
                        actual_contact[field],
                        expected["contact"][field],
                    )
                material_paths.add(material_path)
            mapping.append(
                {
                    "collision_prim": path,
                    "material_prim": material_path,
                    "binding_relationship": str(relationship.GetPath())
                    if relationship
                    else None,
                    "contact": _json_detail(actual_contact),
                }
            )
        assignments[key] = mapping
        if obj["type"] == "button":
            _body(stage, checks, key + ".base", usd_root + "/Base", "kinematic", {})
            _body(stage, checks, key + ".cap", usd_root + "/Cap", "dynamic", expected)
        else:
            _body(
                stage,
                checks,
                key + ".body",
                usd_root,
                expected["motion_type"],
                expected,
            )
        # Additional or incorrectly nested rigid bodies alter PhysX behavior.
        observed_bodies = (
            sorted(
                str(p.GetPath())
                for p in Usd.PrimRange(root_prim)
                if p.HasAPI(UsdPhysics.RigidBodyAPI)
            )
            if root_prim
            else []
        )
        _value(
            checks,
            key + ".body_inventory",
            observed_bodies,
            sorted(paths["rigid_body_prims"]),
        )
        observed_joints = (
            sorted(
                str(prim.GetPath())
                for prim in Usd.PrimRange(root_prim)
                if prim.IsA(UsdPhysics.Joint)
            )
            if root_prim
            else []
        )
        _value(
            checks,
            key + ".joint_inventory",
            observed_joints,
            [usd_root + "/Joint"] if obj["type"] == "button" else [],
        )
        _geometry(stage, checks, obj, usd_root)
        passed = all(check["passed"] for check in checks[start:])
        report["assets"].append(
            {
                "id": key,
                "category": obj.get("category", obj["type"]),
                "geometry_type": obj["type"],
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "expected_physics": expected,
                "failed_checks": [
                    check["id"] for check in checks[start:] if not check["passed"]
                ],
            }
        )
    report["material_assignment"] = {
        "asset_count": len(objects),
        "unique_material_count": len(material_paths),
        "collision_prim_count": sum(len(items) for items in assignments.values()),
        "by_asset": assignments,
    }
    report["coverage"] = {
        "expected_assets": len(objects),
        "catalog_assets": len(catalog_objects),
        "usd_assets": len(actual_ids),
        "passed_assets": sum(asset["passed"] for asset in report["assets"]),
        "categories": dict(
            sorted(
                Counter(
                    obj.get("category", obj["type"]) for obj in objects.values()
                ).items()
            )
        ),
        "scope": "declared object inventory; completeness against the original scene is not evaluated",
    }
    report["passed"] = all(check["passed"] for check in checks)
    report["status"] = "PASS" if report["passed"] else "FAIL"
    report["structural_validation"] = report["status"]
    report["authored_value_validation"] = report["status"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-directory", type=Path, required=True)
    parser.add_argument("--scene-spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    report = inspect_physical_assets(args.scene_directory, scene_spec=args.scene_spec)
    write_json(args.out, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
