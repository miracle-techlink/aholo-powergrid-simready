#!/usr/bin/env python3
"""Fast Isaac Sim batch rendering — decoupled physics/render + mode switching.

Two profiles:
  dev  — 640x360 RealTime, render every N steps (fast iteration)
  prod — 1920x1080 PathTracing, render every step (final video)

Usage:
  # dev: fast preview from a camera orbit
  python fast_render.py scene.usda --profile dev --orbit-center 0,0,1 --frames 120

  # prod: final render with robot trajectory
  python fast_render.py scene.usda --profile prod --frames 900 -o demo_final

  # keyframe trajectory (robot inspection tour)
  python fast_render.py scene.usda --profile dev --keyframes keyframes.json
"""
import os, sys, json, time, argparse

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMNI_KIT_ACCEPT_EULA', 'YES')
os.environ.setdefault('LD_PRELOAD', '/lib/aarch64-linux-gnu/libgomp.so.1')

parser = argparse.ArgumentParser()
parser.add_argument('scene', help='scene USD path')
parser.add_argument('--profile', choices=['dev', 'prod'], default='dev')
parser.add_argument('--frames', type=int, default=120)
parser.add_argument('-o', '--out', default='render_out')
parser.add_argument('--orbit-center', default=None, help='x,y,z orbit center (dev preview)')
parser.add_argument('--orbit-radius', type=float, default=5.0)
parser.add_argument('--orbit-height', type=float, default=1.5)
parser.add_argument('--keyframes', default=None, help='JSON: [{eye,target},...] camera path')
parser.add_argument('--fps', type=int, default=30)
parser.add_argument('--encode', action='store_true', default=True, help='encode mp4 after capture')
parser.add_argument('--no-encode', dest='encode', action='store_false')
args, _ = parser.parse_known_args()

# Profile configs (from INFRA_OPTIMIZATION.md benchmarks)
PROFILES = {
    'dev': dict(res=(640, 360), mode='RealTime', spp=1, subframes=1, every_n=1),
    'prod': dict(res=(1920, 1080), mode='PathTracing', spp=64, subframes=32, every_n=1),
}
cfg = PROFILES[args.profile]

from isaaclab.app import AppLauncher
launcher = AppLauncher(headless=True, offscreen_render=True, experience='isaacsim.exp.base.kit')
simulation_app = launcher.app

import omni.usd, omni.kit.commands, omni.kit.app
import omni.replicator.core as rep
import numpy as np
import carb

ctx = omni.usd.get_context()
ctx.open_stage(os.path.abspath(args.scene))
kit = omni.kit.app.get_app()

# Render mode + quality settings
mode_val = 1 if cfg['mode'] == 'RealTime' else 0  # 0=PathTracing, 1=RealTime
omni.kit.commands.execute('ChangeSetting', path='rtx/renderMode', value=cfg['mode'])
if cfg['mode'] == 'PathTracing':
    omni.kit.commands.execute('ChangeSetting', path='rtx/pathtracing/spp', value=cfg['spp'])
    omni.kit.commands.execute('ChangeSetting', path='rtx/post/tonemap/exposure', value=1.6)
else:
    omni.kit.commands.execute('ChangeSetting', path='rtx/post/tonemap/exposure', value=1.2)

for _ in range(30):
    kit.update()

stage = ctx.get_stage()
W, H = cfg['res']

# Camera + render product
cam = stage.DefinePrim('/World/RenderCam', 'Camera')
from pxr import UsdGeom, Gf
xf = UsdGeom.Xformable(cam)
xf.ClearXformOpOrder()
t_op = xf.AddTranslateOp()
q_op = xf.AddOrientOp()
rp = rep.create.render_product('/World/RenderCam', resolution=(W, H))
rgb_a = rep.AnnotatorRegistry.get_annotator('rgb')
rgb_a.attach([rp])

os.makedirs(args.out, exist_ok=True)

def lookat_quat(eye, target, up=(0, 0, 1)):
    e = np.array(eye, float); t = np.array(target, float)
    f = t - e; f /= np.linalg.norm(f)
    upv = np.array(up, float)
    r_ = np.cross(f, upv); r_ /= np.linalg.norm(r_)
    u = np.cross(r_, f)
    M = np.stack([r_, u, -f], 0)
    tr = M[0, 0] + M[1, 1] + M[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1) * 2
        return 0.25 * s, (M[2, 1] - M[1, 2]) / s, (M[0, 2] - M[2, 0]) / s, (M[1, 0] - M[0, 1]) / s
    elif M[0, 0] > M[1, 1] and M[0, 0] > M[2, 2]:
        s = np.sqrt(1 + M[0, 0] - M[1, 1] - M[2, 2]) * 2
        return (M[2, 1] - M[1, 2]) / s, 0.25 * s, (M[0, 1] + M[1, 0]) / s, (M[0, 2] + M[2, 0]) / s
    elif M[1, 1] > M[2, 2]:
        s = np.sqrt(1 + M[1, 1] - M[0, 0] - M[2, 2]) * 2
        return (M[0, 2] - M[2, 0]) / s, (M[0, 1] + M[1, 0]) / s, 0.25 * s, (M[1, 2] + M[2, 1]) / s
    else:
        s = np.sqrt(1 + M[2, 2] - M[0, 0] - M[1, 1]) * 2
        return (M[1, 0] - M[0, 1]) / s, (M[0, 2] + M[2, 0]) / s, (M[1, 2] + M[2, 1]) / s, 0.25 * s

def camera_path(n):
    """Generate camera poses for n frames."""
    if args.keyframes:
        kfs = json.load(open(args.keyframes))
        # Catmull-Rom style interpolation over keyframes
        pts = np.array([k['eye'] for k in kfs], float)
        tgts = np.array([k['target'] for k in kfs], float)
        # dense path: linear interp with smoothstep
        seg = len(pts) - 1
        out = []
        for i in range(n):
            u = i / max(1, n - 1) * seg
            j = min(int(u), seg - 1)
            v = u - j
            v = v * v * (3 - 2 * v)  # smoothstep
            eye = pts[j] * (1 - v) + pts[j + 1] * v
            tgt = tgts[j] * (1 - v) + tgts[j + 1] * v
            out.append((eye.tolist(), tgt.tolist()))
        return out
    elif args.orbit_center:
        cx, cy, cz = map(float, args.orbit_center.split(','))
        out = []
        for i in range(n):
            th = 2 * np.pi * i / n
            eye = [cx + args.orbit_radius * np.cos(th),
                   cy + args.orbit_radius * np.sin(th),
                   args.orbit_height]
            out.append((eye, [cx, cy, cz]))
        return out
    else:
        raise SystemExit("need --orbit-center or --keyframes")

poses = camera_path(args.frames)

# Warm up renderer
t_op.Set(Gf.Vec3d(*poses[0][0]))
w, x, y, z = lookat_quat(*poses[0])
q_op.Set(Gf.Quatf(w, x, y, z))
kit.update()
rep.orchestrator.step(rt_subframes=cfg['subframes'])
kit.update()

# Batch capture
from PIL import Image
t0 = time.time()
captured = 0
for i, (eye, target) in enumerate(poses):
    t_op.Set(Gf.Vec3d(*eye))
    w, x, y, z = lookat_quat(eye, target)
    q_op.Set(Gf.Quatf(w, x, y, z))
    rep.orchestrator.step(rt_subframes=cfg['subframes'])
    kit.update()
    data = rgb_a.get_data()
    arr = np.ascontiguousarray(data[..., :3], dtype=np.uint8)
    Image.fromarray(arr).save(os.path.join(args.out, f'frame_{i:04d}.png'))
    captured += 1
    if captured % 20 == 0:
        el = time.time() - t0
        print(f'{captured}/{args.frames} ({captured/el:.1f} FPS)', flush=True)

el = time.time() - t0
print(f'capture done: {captured} frames in {el:.1f}s ({captured/el:.1f} FPS)', flush=True)

# Encode BEFORE close() — simulation_app.close() terminates the process
if args.encode:
    import subprocess
    out_mp4 = args.out + '.mp4'
    cmd = ['ffmpeg', '-y', '-framerate', str(args.fps), '-i',
           os.path.join(args.out, 'frame_%04d.png'),
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', out_mp4]
    print('encoding:', ' '.join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    print(f'video: {out_mp4}', flush=True)

simulation_app.close()
