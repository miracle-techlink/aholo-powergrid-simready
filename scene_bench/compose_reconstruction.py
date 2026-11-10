"""Reference raw SfM Gaussian USD beneath a reviewed candidate world transform.

Does not bake positions or rotate SH coefficients separately. Camera requests
are transformed with normalized rotations, not scaled camera axes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-usd', type=Path, required=True)
    ap.add_argument('--alignment', type=Path, required=True)
    ap.add_argument('--trajectory', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    from pxr import Gf, Usd, UsdGeom
    alignment = json.loads(args.alignment.read_text())
    if alignment['new_sha256'] != sha(args.trajectory):
        raise ValueError('Alignment is not bound to this trajectory')
    matrix = np.asarray(alignment['new_to_scene_sim3_column'])
    scale = np.cbrt(np.linalg.det(matrix[:3, :3]))
    rotation = matrix[:3, :3] / scale
    if scale <= 0 or not np.isfinite(matrix).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7):
        raise ValueError('Only finite positive proper similarity transforms supported')
    source = Usd.Stage.Open(str(args.source_usd.resolve()))
    if not source or not source.GetPrimAtPath('/World'):
        raise ValueError('Source has no /World reference root')
    fields = [str(p.GetPath()) for p in source.Traverse() if p.GetTypeName() == 'ParticleField3DGaussianSplat']
    if not fields:
        raise ValueError('Source has no Gaussian field')
    args.out.mkdir(parents=True, exist_ok=False)
    stage_path = args.out / 'scene.usda'
    stage = Usd.Stage.CreateNew(str(stage_path.resolve()))
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    placement = UsdGeom.Xform.Define(stage, '/World/Reconstruction')
    placement.AddTransformOp().Set(Gf.Matrix4d(matrix.T.tolist()))
    content = stage.DefinePrim('/World/Reconstruction/Content', 'Xform')
    content.GetReferences().AddReference(os.path.relpath(args.source_usd.resolve(), args.out.resolve()), '/World')
    stage.GetRootLayer().customLayerData = {
        'coordinateStatus': 'candidate alignment; metric scale not independently surveyed',
        'sourceSHA256': sha(args.source_usd), 'alignmentSHA256': sha(args.alignment)}
    stage.GetRootLayer().Save()
    poses = json.loads(args.trajectory.read_text())['poses']
    views = []
    for p in poses:
        cv = np.asarray(p['camera_to_world'])
        out = np.eye(4)
        out[:3, :3] = rotation @ cv[:3, :3]
        out[:3, 3] = scale * rotation @ cv[:3, 3] + matrix[:3, 3]
        views.append({'id': f"frame_{p['frame_index']:06d}", 'frame_index': p['frame_index'],
                      'resolution': p['resolution'], 'K': p['K'],
                      'camera_to_world_cv': out.tolist(),
                      'source_camera_model': p.get('camera_model'),
                      'source_camera_params': p.get('camera_params'),
                      'timestamp_s': p.get('timestamp_s')})
    camera_path = args.out / 'transformed_camera_requests.json'
    camera_path.write_text(json.dumps({'views': views,
        'warning': 'Only use with distortion-free references or explicitly undistort K/images first.',
        'camera_evidence': {'path': str(args.alignment.resolve()), 'sha256': sha(args.alignment)}}, indent=2)+'\n')
    reopened = Usd.Stage.Open(str(stage_path))
    count = sum(p.GetTypeName() == 'ParticleField3DGaussianSplat' for p in reopened.Traverse())
    if count != len(fields):
        raise ValueError('Composition changed Gaussian field count')
    report = {'scene': str(stage_path.resolve()), 'scene_sha256': sha(stage_path),
              'source': str(args.source_usd.resolve()), 'source_sha256': sha(args.source_usd),
              'alignment': str(args.alignment.resolve()), 'alignment_sha256': sha(args.alignment),
              'camera_requests': str(camera_path.resolve()), 'gaussian_fields': count,
              'transform_policy': 'USD parent similarity; preserve local Gaussian positions, rotations, scales and SH',
              'scale_old_scene_units_per_sfm_unit': float(scale), 'metric_scale_verified': False,
              'render_verified': False}
    (args.out / 'composition.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
