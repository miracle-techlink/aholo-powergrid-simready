"""Author normalized SI properties and explicit per-collider USD bindings.

These operations describe an unvalidated simulation hypothesis. They do not run
PhysX or establish contact-pair friction in the real world. The runtime chooses
its own material-combination rule; the declaration contains per-material values.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any


def bind_contact_material(
    stage: Any,
    material_path: str,
    collider_paths: Sequence[str],
    parameters: Mapping[str, float],
) -> str:
    """Bind one physics material only to the named collision shapes."""
    from pxr import UsdPhysics, UsdShade

    material = UsdShade.Material.Define(stage, material_path)
    api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    api.CreateStaticFrictionAttr(parameters["static_friction"])
    api.CreateDynamicFrictionAttr(parameters["dynamic_friction"])
    api.CreateRestitutionAttr(parameters["restitution"])
    for path in collider_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError(f"Contact binding target is not a collision shape: {path}")
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(
            material, materialPurpose="physics"
        )
    return material_path


def author_rigid_body(prim: Any, physics: Mapping[str, Any]) -> None:
    """Apply body properties on an unscaled local frame; static remains static."""
    from pxr import Gf, UsdPhysics

    if physics["motion_type"] != "static":
        body = UsdPhysics.RigidBodyAPI.Apply(prim)
        body.CreateRigidBodyEnabledAttr(True)
        body.CreateKinematicEnabledAttr(physics["motion_type"] == "kinematic")
    if "mass_kg" in physics:
        mass = UsdPhysics.MassAPI.Apply(prim)
        mass.CreateMassAttr(physics["mass_kg"])
        if "center_of_mass_m" in physics:
            mass.CreateCenterOfMassAttr(Gf.Vec3f(*physics["center_of_mass_m"]))
        if "inertia_kg_m2" in physics:
            mass.CreateDiagonalInertiaAttr(Gf.Vec3f(*physics["inertia_kg_m2"]))
            mass.CreatePrincipalAxesAttr(Gf.Quatf(1))


def author_physics_metadata(prim: Any, physics: Mapping[str, Any]) -> None:
    """Store source declarations in USD so a reader can cross-check manifests."""
    prim.SetCustomDataByKey(
        "simready:physics_json",
        json.dumps(
            dict(physics), sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
    )
