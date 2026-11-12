#!/usr/bin/env python3
"""USD asset validation — quick health check for SimReady compliance.

Checks: metadata, references, geometry, physics, materials, semantics, performance.

Usage:
  python validate_asset.py asset.usda
  python validate_asset.py asset.usda --verbose
  python validate_asset.py *.usda --summary
"""

import os
import sys
import argparse
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # ERROR, WARNING, INFO
    category: (
        str  # metadata, reference, geometry, physics, material, semantic, performance
    )
    message: str
    prim_path: Optional[str] = None

    def __str__(self):
        loc = f" [{self.prim_path}]" if self.prim_path else ""
        return f"[{self.severity}] {self.category}: {self.message}{loc}"


@dataclass
class Report:
    path: str
    issues: List[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "WARNING"]

    def add(self, severity, category, message, prim_path=None):
        self.issues.append(Issue(severity, category, message, prim_path))

    def summary(self):
        e = len(self.errors)
        w = len(self.warnings)
        total = len(self.issues)
        status = "STRUCTURAL_CHECK_PASS" if e == 0 else "FAIL"
        return f"{self.path}: {status} ({e}E/{w}W/{total}T)"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_metadata(stage, report: Report):
    """Check stage metadata: upAxis, metersPerUnit, defaultPrim."""
    from pxr import UsdGeom

    up = UsdGeom.GetStageUpAxis(stage)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    default_prim = stage.GetDefaultPrim()

    if up not in (UsdGeom.Tokens.y, UsdGeom.Tokens.z):
        report.add("WARNING", "metadata", f"upAxis={up} (expected Y or Z)")

    if mpu != 1.0:
        report.add("WARNING", "metadata", f"metersPerUnit={mpu} (expected 1.0)")

    if not default_prim or not default_prim.IsValid():
        report.add("WARNING", "metadata", "no defaultPrim set")

    report.stats["up_axis"] = up
    report.stats["meters_per_unit"] = mpu


def validate_references(stage, report: Report, base_dir: str):
    """Check all references and sublayers resolve correctly."""
    from pxr import UsdUtils

    _, _, unresolved = UsdUtils.ComputeAllDependencies(stage.GetRootLayer().identifier)
    for value in unresolved:
        report.add("ERROR", "reference", f"Unresolved USD dependency: {value}")
    for error in stage.GetCompositionErrors():
        report.add("ERROR", "reference", str(error))
    report.stats["broken_refs"] = len(unresolved)


def validate_geometry(stage, report: Report):
    """Check geometry: extents, normals, topology."""
    from pxr import UsdGeom, Gf
    import math

    mesh_count = 0
    point_count = 0

    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh_count += 1
            mesh = UsdGeom.Mesh(prim)
            points = mesh.GetPointsAttr().Get()
            if points:
                point_count += len(points)
                # Check for degenerate meshes
                if len(points) < 3:
                    report.add(
                        "WARNING",
                        "geometry",
                        f"degenerate mesh (<3 points)",
                        str(prim.GetPath()),
                    )
            # Check extent
            extent = mesh.GetExtentAttr().Get()
            if extent and len(extent) == 2:
                mn, mx = extent
                size = (mx - mn).GetLength()
                if size > 50000:
                    report.add(
                        "WARNING",
                        "performance",
                        f"mesh extent too large: {size:.1f}m",
                        str(prim.GetPath()),
                    )

        elif prim.IsA(UsdGeom.Points):
            points = UsdGeom.Points(prim).GetPointsAttr().Get()
            point_count += len(points) if points is not None else 0

    report.stats["meshes"] = mesh_count
    report.stats["points"] = point_count


def validate_physics(stage, report: Report):
    """Check physics: collision, mass, joints."""
    from pxr import UsdPhysics, Usd

    collision_count = 0
    rigid_body_count = 0
    joint_count = 0
    missing_collision = 0

    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            collision_count += 1
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_body_count += 1
            # Check mass
            mass_api = UsdPhysics.MassAPI(prim)
            mass = mass_api.GetMassAttr().Get()
            if mass is None or mass <= 0:
                report.add(
                    "WARNING",
                    "physics",
                    f"rigid body missing mass",
                    str(prim.GetPath()),
                )

        if prim.IsA(UsdPhysics.Joint):
            joint_count += 1
            # Check joint targets
            joint = UsdPhysics.Joint(prim)
            body0 = joint.GetBody0Rel().GetTargets()
            body1 = joint.GetBody1Rel().GetTargets()
            if not body0 and not body1:
                report.add(
                    "WARNING",
                    "physics",
                    f"joint has no body targets",
                    str(prim.GetPath()),
                )

    # Check for meshes without collision
    from pxr import UsdGeom

    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and not prim.HasAPI(UsdPhysics.CollisionAPI):
            # Check if parent has collision
            parent = prim.GetParent()
            if not parent or not parent.HasAPI(UsdPhysics.CollisionAPI):
                missing_collision += 1

    if missing_collision > 0:
        report.add(
            "INFO",
            "physics",
            f"{missing_collision} meshes without collision (may be visual-only)",
        )

    report.stats["collisions"] = collision_count
    report.stats["rigid_bodies"] = rigid_body_count
    report.stats["joints"] = joint_count


def validate_materials(stage, report: Report):
    """Check materials and textures."""
    from pxr import UsdShade, Sdf

    material_count = 0
    texture_count = 0
    missing_textures = 0

    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material):
            material_count += 1
        elif prim.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(prim)
            for input in shader.GetInputs():
                if input.GetBaseName() == "file":
                    tex_path = input.Get()
                    if tex_path and isinstance(tex_path, Sdf.AssetPath):
                        resolved = tex_path.resolvedPath
                        if not resolved or not os.path.exists(resolved):
                            report.add(
                                "WARNING",
                                "material",
                                f"missing texture: {tex_path.path}",
                                str(prim.GetPath()),
                            )
                            missing_textures += 1
                        else:
                            texture_count += 1

    report.stats["materials"] = material_count
    report.stats["textures"] = texture_count
    report.stats["missing_textures"] = missing_textures


def validate_semantics(stage, report: Report):
    """Check semantic labels."""
    from pxr import Usd, UsdGeom

    labeled = 0
    unlabeled = 0

    for prim in stage.Traverse():
        if prim.GetAttribute("semantic:label").IsValid():
            labeled += 1
        elif prim.IsA(UsdGeom.Mesh) or prim.GetTypeName() in ("Xform", "Scope"):
            unlabeled += 1

    if unlabeled > 0 and labeled == 0:
        report.add("INFO", "semantic", "no semantic labels found")

    report.stats["semantic_labeled"] = labeled
    report.stats["semantic_unlabeled"] = unlabeled


def validate_gaussian_splat(stage, report: Report):
    """Check ParticleField3DGaussianSplat prims."""
    from pxr import Usd

    gsplat_count = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() == "ParticleField3DGaussianSplat":
            gsplat_count += 1
            # Check required attrs
            required = [
                "positions",
                "scales",
                "orientations",
                "opacities",
                "radiance:sphericalHarmonicsDegree",
                "radiance:sphericalHarmonicsCoefficients",
            ]
            for attr_name in required:
                if not prim.GetAttribute(attr_name).IsValid():
                    report.add(
                        "ERROR",
                        "gaussian_splat",
                        f"missing required attr: {attr_name}",
                        str(prim.GetPath()),
                    )

    report.stats["gaussian_splats"] = gsplat_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def validate_asset(path: str, verbose: bool = False) -> Report:
    """Run all validators on a USD asset."""
    from pxr import Usd

    report = Report(path=path)

    if not os.path.exists(path):
        report.add("ERROR", "metadata", f"file not found: {path}")
        return report

    try:
        stage = Usd.Stage.Open(path)
    except Exception as e:
        report.add("ERROR", "metadata", f"failed to open: {e}")
        return report

    if not stage:
        report.add("ERROR", "metadata", "USD stage could not be opened")
        return report
    base_dir = os.path.dirname(os.path.abspath(path))

    validate_metadata(stage, report)
    validate_references(stage, report, base_dir)
    validate_geometry(stage, report)
    validate_physics(stage, report)
    validate_materials(stage, report)
    validate_semantics(stage, report)
    validate_gaussian_splat(stage, report)

    # Summary stats
    prim_count = sum(1 for _ in stage.Traverse())
    report.stats["total_prims"] = prim_count

    return report


def print_report(report: Report, verbose: bool = False):
    """Pretty-print validation report."""
    print(f"\n{'=' * 60}")
    print(f"Asset: {report.path}")
    print(f"{'=' * 60}")

    # Stats
    print(f"\nStats:")
    for k, v in sorted(report.stats.items()):
        print(f"  {k}: {v}")

    # Issues
    if report.errors:
        print(f"\nErrors ({len(report.errors)}):")
        for issue in report.errors:
            print(f"  {issue}")

    if report.warnings:
        print(f"\nWarnings ({len(report.warnings)}):")
        for issue in report.warnings:
            print(f"  {issue}")

    if verbose and report.issues:
        infos = [i for i in report.issues if i.severity == "INFO"]
        if infos:
            print(f"\nInfo ({len(infos)}):")
            for issue in infos:
                print(f"  {issue}")

    if not report.issues:
        print("\n✅ No issues found")

    print(f"\n{report.summary()}")


def main():
    ap = argparse.ArgumentParser(description="USD asset validator")
    ap.add_argument("files", nargs="+", help="USD files to validate")
    ap.add_argument("--verbose", "-v", action="store_true", help="Show INFO issues")
    ap.add_argument("--summary", "-s", action="store_true", help="Summary only")
    args = ap.parse_args()

    reports = []
    for f in args.files:
        r = validate_asset(f, args.verbose)
        reports.append(r)
        if not args.summary:
            print_report(r, args.verbose)

    if args.summary:
        print(f"\n{'=' * 60}")
        print("Summary:")
        for r in reports:
            print(f"  {r.summary()}")

    # Exit code: 0 if all pass, 1 if any errors
    sys.exit(0 if all(len(r.errors) == 0 for r in reports) else 1)


if __name__ == "__main__":
    main()
