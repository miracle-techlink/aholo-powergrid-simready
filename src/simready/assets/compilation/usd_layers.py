"""Author USD object and collision layers from validated declarations."""

import numpy as np

from simready.assets.compilation.spec_validation import _bounds
from simready.assets.physics.physical_authoring import (
    author_physics_metadata,
    author_rigid_body,
    bind_contact_material,
)


def _stage(path, root_path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, root_path)
    stage.SetDefaultPrim(root.GetPrim())
    return stage


def _cube(stage, path, bounds, invisible=False):
    from pxr import Gf, UsdGeom, UsdPhysics

    low, high = _bounds(bounds)
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(*map(float, (low + high) / 2)))
    cube.AddScaleOp().Set(Gf.Vec3f(*map(float, high - low)))
    if invisible:
        cube.CreateVisibilityAttr("invisible")
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    return cube


def _material(stage, path, color, roughness):
    from pxr import Gf, Sdf, UsdShade

    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _bind_contact_material(stage, root, parameters):
    from pxr import Usd, UsdPhysics

    # Background geometry has one preset, scoped to its subtree. It must never
    # replace a semantic object's separately declared material.
    colliders = [
        str(prim.GetPath())
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root))
        if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    bind_contact_material(stage, root + "/ContactPreset", colliders, parameters)


def _write_objects(path, objects, contact):
    from pxr import Gf, UsdGeom, UsdPhysics, UsdShade

    stage = _stage(path, "/Objects")
    # Retain the legacy preset prim for schema-v1 readers. Bind only object-local
    # materials below, after shapes exist; a global pass would overwrite them.
    _bind_contact_material(stage, "/Objects", contact)
    for obj in objects:
        root = "/Objects/" + obj["id"]
        body = UsdGeom.Xform.Define(stage, root)
        body.GetPrim().SetCustomDataByKey("simready:semantic_category", obj["category"])
        body.GetPrim().SetCustomDataByKey("simready:geometry_type", obj["type"])
        body.GetPrim().SetCustomDataByKey("simready:object_id", obj["id"])
        if not obj["visible"]:
            body.CreateVisibilityAttr("invisible")
        author_physics_metadata(body.GetPrim(), obj["physics"])
        material = _material(
            stage, root + "/Looks/Surface", obj["color"], obj["roughness"]
        )
        if obj["type"] == "box":
            low, high = _bounds(obj["bounds_world"])
            center = (low + high) / 2
            body.AddTranslateOp().Set(Gf.Vec3d(*center.tolist()))
            shape = _cube(
                stage,
                root + "/Shape",
                {
                    "min": (low - center).tolist(),
                    "max": (high - center).tolist(),
                },
            )
            author_rigid_body(body.GetPrim(), obj["physics"])
            UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(material)
            obj["prim_path"] = "/World" + root + "/Shape"
            colliders = [root + "/Shape"]
            material_path = bind_contact_material(
                stage,
                root + "/Looks/PhysicsMaterial",
                colliders,
                obj["physics"]["contact"],
            )
            obj["physics_binding"] = {
                "material_prim": "/World" + material_path,
                "collision_prims": ["/World" + p for p in colliders],
                "rigid_body_prims": ["/World" + root]
                if obj["physics"]["motion_type"] != "static"
                else [],
            }
            continue
        position, axis = np.array(obj["position_world"]), obj["axis"]
        vector = np.eye(3)["XYZ".index(axis)]
        base_center = position - vector * (obj["travel"] + obj["cap_height"] * 1.5)
        base = UsdGeom.Xform.Define(stage, root + "/Base")
        base.AddTranslateOp().Set(Gf.Vec3d(*base_center.tolist()))
        UsdPhysics.RigidBodyAPI.Apply(base.GetPrim()).CreateKinematicEnabledAttr(True)
        cap = UsdGeom.Xform.Define(stage, root + "/Cap")
        cap.AddTranslateOp().Set(Gf.Vec3d(*position.tolist()))
        author_rigid_body(cap.GetPrim(), obj["physics"])
        for role, radius, height in [
            ("Base", obj["radius"] * 1.3, obj["cap_height"]),
            ("Cap", obj["radius"], obj["cap_height"]),
        ]:
            shape = UsdGeom.Cylinder.Define(stage, root + "/" + role + "/Shape")
            shape.CreateAxisAttr(axis)
            shape.CreateRadiusAttr(radius)
            shape.CreateHeightAttr(height)
            UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
            UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(material)
        joint = UsdPhysics.PrismaticJoint.Define(stage, root + "/Joint")
        joint.CreateBody0Rel().SetTargets([root + "/Base"])
        joint.CreateBody1Rel().SetTargets([root + "/Cap"])
        joint.CreateAxisAttr(axis)
        joint.CreateLocalPos0Attr(Gf.Vec3f(*(position - base_center).tolist()))
        joint.CreateLocalPos1Attr(Gf.Vec3f(0))
        joint.CreateLocalRot0Attr(Gf.Quatf(1))
        joint.CreateLocalRot1Attr(Gf.Quatf(1))
        joint.CreateLowerLimitAttr(-obj["travel"])
        joint.CreateUpperLimitAttr(0.0)
        joint.CreateCollisionEnabledAttr(False)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
        drive.CreateTypeAttr("force")
        drive.CreateTargetPositionAttr(0.0)
        drive.CreateStiffnessAttr(obj["stiffness"])
        drive.CreateDampingAttr(obj["damping"])
        drive.CreateMaxForceAttr(obj["max_force"])
        obj.update(
            base_prim="/World" + root + "/Base",
            cap_prim="/World" + root + "/Cap",
            joint_prim="/World" + root + "/Joint",
        )
        colliders = [root + "/Base/Shape", root + "/Cap/Shape"]
        material_path = bind_contact_material(
            stage, root + "/Looks/PhysicsMaterial", colliders, obj["physics"]["contact"]
        )
        obj["physics_binding"] = {
            "material_prim": "/World" + material_path,
            "collision_prims": ["/World" + p for p in colliders],
            "rigid_body_prims": [obj["base_prim"], obj["cap_prim"]],
        }
    stage.GetRootLayer().Save()


def _write_collision(path, bounds, floor, proxies, contact):
    from pxr import UsdGeom, UsdPhysics

    stage = _stage(path, "/Collision")
    if floor:
        low, high = _bounds(bounds)
        normal, offset = np.array(floor["normal"]), floor["offset"]
        vertices = []
        for x, y in [
            (low[0], low[1]),
            (high[0], low[1]),
            (high[0], high[1]),
            (low[0], high[1]),
        ]:
            vertices.append(
                (
                    float(x),
                    float(y),
                    float(-(normal[0] * x + normal[1] * y + offset) / normal[2]),
                )
            )
        mesh = UsdGeom.Mesh.Define(stage, "/Collision/Floor")
        mesh.CreatePointsAttr(vertices)
        mesh.CreateFaceVertexCountsAttr([3, 3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    for index, box in enumerate(proxies):
        _cube(stage, f"/Collision/Voxel_{index:05d}", box, invisible=True)
    _bind_contact_material(stage, "/Collision", contact)
    stage.GetRootLayer().Save()
