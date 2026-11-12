"""A flange-mounted passive spring plunger for simulated button pressing.

The axial joint supplies mechanical compliance; the blue cap is a rigid contact
proxy for a replaceable elastomer tip. No deformable-rubber or insulation rating
is implied. All dimensions are design estimates in the current scene scale.
"""

import numpy as np


def add_payload_inertia(body, mass, center, radius, length):
    from pxr import Gf, UsdPhysics
    from scipy.spatial.transform import Rotation

    api = UsdPhysics.MassAPI(body)
    old_mass = float(api.GetMassAttr().Get())
    old_center = np.array(api.GetCenterOfMassAttr().Get(), float)
    q = api.GetPrincipalAxesAttr().Get()
    rot = Rotation.from_quat([*q.GetImaginary(), q.GetReal()]).as_matrix()
    tensor = rot @ np.diag(np.array(api.GetDiagonalInertiaAttr().Get())) @ rot.T
    center = np.asarray(center, float)
    combined = (old_mass * old_center + mass * center) / (old_mass + mass)

    def parallel(m, d):
        return m * ((d @ d) * np.eye(3) - np.outer(d, d))

    own = np.diag(
        [
            mass * radius**2 / 2,
            mass * (3 * radius**2 + length**2) / 12,
            mass * (3 * radius**2 + length**2) / 12,
        ]
    )
    tensor += (
        parallel(old_mass, old_center - combined)
        + own
        + parallel(mass, center - combined)
    )
    values, vectors = np.linalg.eigh(tensor)
    if np.linalg.det(vectors) < 0:
        vectors[:, 0] *= -1
    quat = Rotation.from_matrix(vectors).as_quat()
    api.CreateMassAttr(old_mass + mass)
    api.CreateCenterOfMassAttr(Gf.Vec3f(*combined))
    api.CreateDiagonalInertiaAttr(Gf.Vec3f(*values))
    api.CreatePrincipalAxesAttr(Gf.Quatf(float(quat[3]), Gf.Vec3f(*quat[:3])))
    return dict(
        old_mass_kg=old_mass,
        new_mass_kg=old_mass + mass,
        added_mass_kg=mass,
        parallel_axis_inertia_updated=True,
    )


def build_spring_plunger(stage, flange_path, cfg):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, UsdShade

    offset = np.array(cfg["flange_offset_m"], float)
    radius = float(cfg["tip_radius_m"])
    travel = float(cfg["travel_m"])
    assert (
        offset[0] > 0.08
        and np.allclose(offset[1:], 0)
        and 0 < radius < 0.01
        and 0 < travel < 0.01
    )

    def material(name, color, rough=0.35, metal=0.0):
        p = "/World/TaskToolLooks/" + name
        mat = UsdShade.Material.Define(stage, p)
        sh = UsdShade.Shader.Define(stage, p + "/Surface")
        sh.CreateIdAttr("UsdPreviewSurface")
        c = np.array(color)
        c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        return mat

    silver = material("SleeveMetal", [0.56, 0.60, 0.65], 0.23, 0.7)
    black = material("InsulatingCollar", [0.11, 0.13, 0.16], 0.4)
    blue = material("ReplaceableSoftTip", [0.10, 0.37, 0.76], 0.7)
    green = material("TravelIndicator", [0.17, 0.65, 0.40], 0.4)

    def cylinder(path, center, r, h, mat, collision=False):
        s = UsdGeom.Cylinder.Define(stage, path)
        s.CreateAxisAttr("X")
        s.CreateRadiusAttr(r)
        s.CreateHeightAttr(h)
        s.AddTranslateOp().Set(Gf.Vec3d(*center))
        UsdShade.MaterialBindingAPI.Apply(s.GetPrim()).Bind(mat)
        if collision:
            UsdPhysics.CollisionAPI.Apply(s.GetPrim())
        return s

    cylinder(flange_path + "/TaskToolMount", [0.052, 0, 0], 0.016, 0.006, black, True)
    cylinder(
        flange_path + "/TaskToolSleeve", [0.066, 0, 0], 0.0085, 0.026, silver, True
    )
    cylinder(flange_path + "/TaskToolSeal", [0.080, 0, 0], 0.006, 0.003, black)
    payload = add_payload_inertia(
        stage.GetPrimAtPath(flange_path),
        cfg["housing_mass_kg"],
        [0.065, 0, 0],
        0.010,
        0.030,
    )
    path = "/World/FrankaArm/TaskPressPlunger"
    x = UsdGeom.Xform.Define(stage, path)
    x.SetResetXformStack(True)
    flange = np.array(
        UsdGeom.Xformable(
            stage.GetPrimAtPath(flange_path)
        ).ComputeLocalToWorldTransform(0)
    ).T
    pose = flange.copy()
    pose[:3, 3] = (flange @ np.r_[offset, 1])[:3]
    x.AddTransformOp().Set(Gf.Matrix4d(pose.T.tolist()))
    UsdPhysics.RigidBodyAPI.Apply(x.GetPrim()).CreateKinematicEnabledAttr(False)
    UsdPhysics.MassAPI.Apply(x.GetPrim()).CreateMassAttr(cfg["plunger_mass_kg"])
    cylinder(path + "/TaskPressShaft", [-0.012, 0, 0], 0.0025, 0.024, silver, True)
    cylinder(path + "/TravelBand", [-0.017, 0, 0], 0.0027, 0.003, green)
    tip = UsdGeom.Sphere.Define(stage, path + "/TaskPressTip")
    tip.CreateRadiusAttr(radius)
    UsdShade.MaterialBindingAPI.Apply(tip.GetPrim()).Bind(blue)
    UsdPhysics.CollisionAPI.Apply(tip.GetPrim())
    contact = UsdShade.Material.Define(stage, "/World/TaskToolLooks/Contact")
    physics = UsdPhysics.MaterialAPI.Apply(contact.GetPrim())
    physics.CreateStaticFrictionAttr(0.9)
    physics.CreateDynamicFrictionAttr(0.8)
    physics.CreateRestitutionAttr(0.0)
    UsdShade.MaterialBindingAPI.Apply(tip.GetPrim()).Bind(
        contact, materialPurpose="physics"
    )
    for prim in [tip.GetPrim(), stage.GetPrimAtPath(path + "/TaskPressShaft")]:
        prim.AddAppliedSchema("PhysxCollisionAPI")
        prim.CreateAttribute(
            "physxCollision:contactOffset", Sdf.ValueTypeNames.Float
        ).Set(0.0004)
        prim.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(
            0.0
        )
    name = "TaskPressCompliance"
    joint = UsdPhysics.PrismaticJoint.Define(stage, "/World/FrankaArm/" + name)
    joint.CreateBody0Rel().SetTargets([flange_path])
    joint.CreateBody1Rel().SetTargets([path])
    joint.CreateAxisAttr("X")
    joint.CreateLocalPos0Attr(Gf.Vec3f(*offset))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0))
    joint.CreateLocalRot0Attr(Gf.Quatf(1))
    joint.CreateLocalRot1Attr(Gf.Quatf(1))
    joint.CreateLowerLimitAttr(-travel)
    joint.CreateUpperLimitAttr(0.0)
    joint.CreateCollisionEnabledAttr(False)
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
    drive.CreateTypeAttr("force")
    drive.CreateTargetPositionAttr(0.0)
    drive.CreateStiffnessAttr(cfg["spring_N_m"])
    drive.CreateDampingAttr(cfg["damping_N_s_m"])
    drive.CreateMaxForceAttr(20.0)
    for key, ty, value in [
        (
            "simready:toolKind",
            Sdf.ValueTypeNames.String,
            "passive_axial_spring_plunger",
        ),
        ("simready:travel_m", Sdf.ValueTypeNames.Float, travel),
        ("simready:softTipDeformationSimulated", Sdf.ValueTypeNames.Bool, False),
    ]:
        x.GetPrim().CreateAttribute(key, ty).Set(value)
    x.GetPrim().CreateRelationship("simready:flangeBody").SetTargets([flange_path])
    x.GetPrim().CreateAttribute(
        "simready:nominalOffset", Sdf.ValueTypeNames.Float3
    ).Set(Gf.Vec3f(*offset))
    return dict(
        flange_body=flange_path,
        contact_body=path,
        joint_name=name,
        travel_m=travel,
        nominal_offset_m=offset.tolist(),
        tip_radius_m=radius,
        spring_N_m=cfg["spring_N_m"],
        damping_N_s_m=cfg["damping_N_s_m"],
        payload=payload,
        plunger_mass_kg=cfg["plunger_mass_kg"],
        rubber_deformation_simulated=False,
    )
