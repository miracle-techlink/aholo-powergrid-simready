#!/usr/bin/env python3
"""Direct RGB-annotator capture variant; camera requests are NOT verified GT.

Run with Isaac venv Python, DISPLAY=:1, LD_PRELOAD=libgomp.so.1. Camera JSON:
{"resolution":[720,1280],"K":[[840,0,360],[0,840,640],[0,0,1]],
 "views":[{"id":"yaw0","eye":[0,0,1.1],"target":[1,0,1.1]}]}
Each view may instead provide camera_to_world_cv (column-vector OpenCV x-right,
y-down, z-forward). A static pose is held for repeated captures before export.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import traceback

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(name, '1')
os.environ.setdefault('PYTORCH_JIT', '0')
os.environ.setdefault('OMNI_KIT_ACCEPT_EULA', 'YES')

import numpy as np

DEFAULT_SCENE = Path('/home/nvidia/Desktop/星巴克重建资产_20261108/02_星巴克实拍重建/scene.usda')
LOCK = Path('/tmp/g2_starbucks_tour.gpu.lock')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def prepare_camera(view, document):
    w, h = view.get('resolution', document.get('resolution', [720, 1280]))
    k = np.asarray(view.get('K', document.get('K')), dtype=float)
    if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ValueError('A finite3x3 pinhole K with positive fx/fy is required')
    if abs(k[0, 1]) > 1e-10 or not np.allclose(k[2], [0, 0, 1]):
        raise ValueError('Skew/nonstandard homogeneous K unsupported')
    if 'camera_to_world_cv' in view:
        c2w = np.asarray(view['camera_to_world_cv'], dtype=float)
    else:
        eye, target = np.asarray(view['eye'], dtype=float), np.asarray(view['target'], dtype=float)
        up = np.asarray(view.get('up', [0, 0, 1]), dtype=float)
        forward = target - eye
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        c2w = np.eye(4)
        c2w[:3, :3] = np.column_stack([right, down, forward])
        c2w[:3, 3] = eye
    if c2w.shape != (4, 4) or not np.isfinite(c2w).all() or not np.allclose(c2w[3], [0, 0, 0, 1]):
        raise ValueError('Invalid camera_to_world_cv')
    rotation = c2w[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not math.isclose(np.linalg.det(rotation), 1, abs_tol=1e-6):
        raise ValueError('Camera rotation must be orthonormal and right-handed')
    return {'id': view['id'], 'resolution': [int(w), int(h)], 'K': k.tolist(),
            'camera_to_world_cv': c2w.tolist(), 'world_to_camera_cv': np.linalg.inv(c2w).tolist(),
            'camera_evidence': view.get('camera_evidence', document.get('camera_evidence')),
            'camera_status': 'unverified_request', 'near_far': view.get('near_far', [.03, 100.]),
            'projection': 'pinhole; no lens distortion', 'pixel_origin': 'top left; K as requested'}


def check_cpu_quota():
    line = next(x for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
    directory = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
    observed = []
    while str(directory).startswith('/sys/fs/cgroup'):
        p = directory / 'cpu.max'
        if p.exists():
            limit = p.read_text().strip()
            observed.append({'path': str(p), 'value': limit})
            if not limit.startswith('max '):
                raise RuntimeError(f'Hard CPU quota found at {p}: {limit}; start without hard quota')
        if directory == Path('/sys/fs/cgroup'):
            break
        directory = directory.parent
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, default=DEFAULT_SCENE)
    parser.add_argument('--cameras', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--only-id')
    parser.add_argument('--subframes', type=int, default=2)
    parser.add_argument('--settle-captures', type=int, default=3)
    parser.add_argument('--depth', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--settings-json', type=Path,
                        help='Explicit RTX setting overrides, recorded in render provenance')
    parser.add_argument('--sun-intensity', type=float, default=1800.)
    parser.add_argument('--dome-intensity', type=float, default=200.)
    args = parser.parse_args()
    args.scene, args.cameras, args.out = args.scene.resolve(), args.cameras.resolve(), args.out.resolve()
    document = json.loads(args.cameras.read_text())
    overrides = json.loads(args.settings_json.read_text()) if args.settings_json else {}
    if not isinstance(overrides, dict) or any(not key.startswith(('/rtx/', '/rtx-transient/')) for key in overrides):
        raise ValueError('Renderer overrides must contain /rtx/ or /rtx-transient/ setting paths')
    camera_evidence = document.get('camera_evidence')
    if camera_evidence and sha(Path(camera_evidence['path'])) != camera_evidence['sha256']:
        raise ValueError('Camera evidence bytes do not match the bound SHA256')
    views = [prepare_camera(v, document) for v in document['views'] if not args.only_id or v['id'] == args.only_id]
    for view in views:
        evidence = view.get('camera_evidence')
        if evidence and sha(Path(evidence['path'])) != evidence['sha256']:
            raise ValueError(f'Changed camera evidence for {view["id"]}')
    if not views or len({v['id'] for v in views}) != len(views):
        raise ValueError('Views must be nonempty with unique IDs')
    if any('/' in v['id'] or '..' in v['id'] for v in views):
        raise ValueError('View IDs must be simple filenames')
    if args.settle_captures < 2 or args.subframes < 1:
        raise ValueError('At least2 static settle captures and1 subframe required')
    if args.validate_only:
        print(json.dumps({'camera_requests': views, 'verified': False})); return
    if args.out.exists():
        raise FileExistsError('Use a fresh render output directory')
    quota = check_cpu_quota()
    lock = LOCK.open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    args.out.mkdir(parents=True)
    save(args.out / 'request.json', {'scene': str(args.scene), 'camera_file': str(args.cameras),
          'camera_file_sha256': sha(args.cameras), 'views': views, 'cpu_quota_chain': quota,
          'status': 'starting', 'camera_match_verified': False})
    app = None
    save(args.out / 'render_status.json', {'status': 'starting', 'capture_backend': 'rgb_annotator_direct'})
    try:
        from isaaclab.app import AppLauncher
        launcher = AppLauncher(headless=True, offscreen_render=True, experience='isaacsim.exp.base.kit')
        app = launcher.app
        import carb
        import omni.usd
        import omni.kit.app
        from pxr import UsdGeom, UsdLux, Gf, Sdf
        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate('omni.replicator.core', True)
        import omni.replicator.core as rep
        from omni.kit.viewport.window import get_viewport_window_instances
        for window in get_viewport_window_instances(None):
            window.viewport_api.updates_enabled = False
        stage = omni.usd.get_context().get_stage()
        stage.GetRootLayer().subLayerPaths = [str(args.scene)]
        stage.SetMetadata('upAxis', 'Z'); stage.SetMetadata('metersPerUnit', 1.)
        assets = []
        todo, seen = [args.scene], set()
        while todo:
            path = todo.pop().resolve()
            if str(path) in seen:
                continue
            seen.add(str(path)); assets.append({'path': str(path), 'sha256': sha(path)})
            layer = Sdf.Layer.FindOrOpen(str(path))
            for ref in layer.GetExternalReferences():
                if not ref:
                    # Internal USD references have an empty asset path; they
                    # resolve within this layer, not to its parent directory.
                    continue
                if '://' in ref or '[' in ref:
                    raise ValueError('Only local unpackaged scene layers permitted')
                todo.append(path.parent / ref)
        candidate_hash = digest(sorted(assets, key=lambda a: (a['path'], a['sha256'])))
        settings = carb.settings.get_settings()
        settings_values = {'/rtx/rendermode': 'RealTimePathTracing', '/rtx/post/aa/op': 4,
            '/rtx/post/dlss/execMode': 2, '/rtx/dldenoiser/enabled': True,
            '/rtx/dldenoiser/responsiveDenoising': True,
            '/rtx/rtpt/gaussian/accumulatedDepth/enabled': True,
            '/rtx/rtpt/gaussian/accumulatedAlbedo/enabled': True,
            '/rtx/rtpt/gaussian/maxGaussiansToAccumulate': 48}
        settings_values.update(overrides)
        for key, value in settings_values.items():
            settings.set(key, value)
        camera = UsdGeom.Camera.Define(stage, '/World/BenchCamera')
        op = UsdGeom.Xformable(camera).AddTransformOp()
        sun = UsdLux.DistantLight.Define(stage, '/World/BenchSun')
        sun.CreateIntensityAttr(args.sun_intensity)
        sun.CreateAngleAttr(12.)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-45, 20, 30))
        UsdLux.DomeLight.Define(stage, '/World/BenchFill').CreateIntensityAttr(args.dome_intensity)
        if len({tuple(v['resolution']) for v in views}) != 1:
            raise ValueError('Use separate runs for different resolutions')
        rp = rep.create.render_product(str(camera.GetPath()), tuple(views[0]['resolution']))
        from PIL import Image
        rgb = rep.AnnotatorRegistry.get_annotator('rgb')
        rgb.attach([rp])
        depth = rep.AnnotatorRegistry.get_annotator('distance_to_image_plane') if args.depth else None
        if depth:
            depth.attach([rp])
        results = []
        for index, view in enumerate(views):
            started = time.monotonic()
            camera_evidence = view.get('camera_evidence')
            w, h = view['resolution']; k = np.asarray(view['K'])
            focal = 20.
            horizontal, vertical = focal * w / k[0, 0], focal * h / k[1, 1]
            camera.CreateFocalLengthAttr(focal)
            camera.CreateHorizontalApertureAttr(horizontal)
            camera.CreateVerticalApertureAttr(vertical)
            camera.CreateHorizontalApertureOffsetAttr((w / 2 - k[0, 2]) * horizontal / w)
            camera.CreateVerticalApertureOffsetAttr((k[1, 2] - h / 2) * vertical / h)
            camera.CreateClippingRangeAttr(Gf.Vec2f(*view['near_far']))
            cv = np.asarray(view['camera_to_world_cv'])
            usd_column = cv @ np.diag([1., -1., -1., 1.])
            op.Set(Gf.Matrix4d(usd_column.T.tolist()))
            for _ in range(args.settle_captures):
                rep.orchestrator.step(rt_subframes=args.subframes, pause_timeline=True,
                                      delta_time=1/30, wait_for_render=True)
            rep.orchestrator.wait_until_complete()
            raw_index = (index + 1) * args.settle_captures - 1
            # Read only after the same3 static captures. No extra app.update/step:
            # RGB and depth belong to the same render product and unchanged pose.
            rgba = np.array(rgb.get_data(), copy=True)
            if rgba.dtype != np.uint8 or rgba.shape not in ((h, w, 3), (h, w, 4)):
                raise ValueError(f'Unexpected RGB buffer dtype/shape: {rgba.dtype}/{rgba.shape}')
            if not np.isfinite(rgba).all():
                raise ValueError('Nonfinite RGB data')
            color = np.ascontiguousarray(rgba[:, :, :3])
            if not np.any(color) or float(color.std()) < .01:
                raise ValueError('Black or constant RGB buffer')
            image = args.out / (view['id'] + '.png')
            temporary_image = image.with_suffix('.tmp.png')
            Image.fromarray(color, mode='RGB').save(temporary_image)
            temporary_image.replace(image)
            with Image.open(image) as decoded:
                decoded.load()
                if decoded.mode != 'RGB' or decoded.size != (w, h) or not np.array_equal(np.asarray(decoded), color):
                    raise ValueError('PNG roundtrip failed')
            capture_check = {'backend': 'rgb_annotator_direct', 'input_shape': list(rgba.shape),
                'input_dtype': str(rgba.dtype), 'output_mode': 'RGB', 'rgba_alpha_discarded': rgba.shape[2] == 4,
                'alpha_range': [int(rgba[:, :, 3].min()), int(rgba[:, :, 3].max())] if rgba.shape[2] == 4 else None,
                'rgb_mean': float(color.mean()), 'rgb_std': float(color.std()),
                'finite': True, 'nonblack': True, 'png_decode_exact': True,
                'pose_held_for_captures': args.settle_captures, 'pose_not_changed_between_rgb_and_depth': True}

            view_path = args.out / (view['id'] + '.camera.json')
            save(view_path, view)
            depth_record = None
            if depth:
                z = np.asarray(depth.get_data(), dtype=np.float32)
                if z.shape != (h, w) or not np.any(np.isfinite(z) & (z > 0)):
                    raise ValueError(f'Invalid depth shape/content: {z.shape}')
                zpath = args.out / (view['id'] + '.depth.npy')
                np.save(zpath, z)
                valid = np.isfinite(z) & (z > 0) & (z < view['near_far'][1])
                depth_record = {'path': str(zpath), 'sha256': sha(zpath), 'shape': list(z.shape),
                                'valid_fraction': float(valid.mean()), 'kind': 'distance_to_image_plane',
                                'units': 'meters', 'geometry_verified': False,
                                'warning': 'Renderer Gaussian depth is a localization proxy, not solid-surface or collision GT.'}
            result = {'view_id': view['id'], 'render': str(image), 'render_sha256': sha(image),
                'candidate_digest': candidate_hash, 'assets': assets,
                'camera_evidence_sha256': camera_evidence['sha256'] if camera_evidence else sha(args.cameras),
                'camera_evidence': camera_evidence, 'camera_request': str(args.cameras),
                'camera_request_sha256': sha(args.cameras),
                'resolved_camera': str(view_path), 'resolved_camera_sha256': sha(view_path),
                'camera_match_verified': False, 'camera_status': 'unverified_request',
                'renderer_script_sha256': sha(Path(__file__)), 'render_settings': settings_values,
                'effective_render_settings': {key: settings.get(key) for key in settings_values},
                'authored_lighting': {'sun_intensity': args.sun_intensity, 'dome_intensity': args.dome_intensity},
                'raw_capture_index': raw_index, 'static_settle_captures': args.settle_captures,
                'readback_note': 'Direct RGB/depth reads after static settling; same render product and unchanged camera for all captures.',
                'capture_validation': capture_check,
                'depth': depth_record, 'seconds': time.monotonic() - started,
                'created_at': datetime.now(timezone.utc).isoformat()}
            save(args.out / (view['id'] + '.render_provenance.json'), result)
            results.append(result)
            save(args.out / 'render_manifest.json', {'views': results, 'camera_match_verified': False})
            print('VIEW_READY', view['id'], str(image), flush=True)
        rgb.detach([rp])
        if depth:
            depth.detach([rp])
        stage.GetRootLayer().Export(str(args.out / 'render_stage.usda'))
        save(args.out / 'render_status.json', {'status': 'complete', 'capture_backend': 'rgb_annotator_direct', 'view_count': len(results), 'manifest_sha256': sha(args.out / 'render_manifest.json')})
    except BaseException as error:
        # SimulationApp.close may terminate with exit0; retain explicit failure evidence first.
        save(args.out / 'render_failure.json', {'error': repr(error), 'traceback': traceback.format_exc(),
             'raw_files': [str(p) for p in (args.out / 'raw_frames').glob('*')],
             'created_at': datetime.now(timezone.utc).isoformat()})
        save(args.out / 'render_status.json', {'status': 'failed', 'error': repr(error), 'capture_backend': 'rgb_annotator_direct'})
        print('RENDER_FAILED', traceback.format_exc(), flush=True)
        raise
    finally:
        if app is not None:
            app.close()
        lock.close()


if __name__ == '__main__':
    main()
