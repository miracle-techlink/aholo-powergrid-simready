#!/usr/bin/env python3
"""Profile Isaac Sim load & render phases: wall time + CPU + GPU per phase.

Phases measured:
  P0 app_start   — AppLauncher init (kit boot, extension load)
  P1 stage_open  — ctx.open_stage() + settle updates
  P2 first_frame — camera + render product + annotator attach + first step
  P3 render_loop — N steady-state frames (camera moves each frame)
  P4 encode+exit — (skipped, just close)

Outputs JSON to --out for analysis. Runs nvidia-smi polling in a thread.
"""
import os, sys, time, json, threading, subprocess, argparse

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMNI_KIT_ACCEPT_EULA', 'YES')
os.environ.setdefault('LD_PRELOAD', '/lib/aarch64-linux-gnu/libgomp.so.1')

parser = argparse.ArgumentParser()
parser.add_argument('--scene', default='/tmp/scene_test.usda')
parser.add_argument('--frames', type=int, default=60)
parser.add_argument('--res', type=int, nargs=2, default=(640, 360))
parser.add_argument('--out', default='profile_result.json')
args, _ = parser.parse_known_args()

T0 = time.time()
phases = {}

# --- GPU sampler thread (nvidia-smi polling) ---
gpu_samples = []
def gpu_sampler():
    while not stop_flag.is_set():
        try:
            r = subprocess.run(
                ['nvidia-smi', '--query-gpu=utilization.gpu,utilization.memory,memory.used,power.draw',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=2)
            parts = [x.strip() for x in r.stdout.strip().split(',')]
            gpu_samples.append({'t': time.time() - T0,
                                'gpu%': int(parts[0]), 'mem%': int(parts[1]),
                                'mem_mb': int(parts[2]), 'watt': parts[3]})
        except Exception:
            pass
        time.sleep(0.2)

# --- CPU sampler thread ---
cpu_samples = []
def cpu_sampler():
    import psutil
    psutil.cpu_percent(percpu=True)  # prime
    while not stop_flag.is_set():
        cpu_samples.append({'t': time.time() - T0,
                            'cpu%': psutil.cpu_percent(),
                            'cores': psutil.cpu_percent(percpu=True),
                            'rss_gb': psutil.Process().memory_info().rss / 1e9})
        time.sleep(0.2)

stop_flag = threading.Event()
gt = threading.Thread(target=gpu_sampler, daemon=True); gt.start()
ct = threading.Thread(target=cpu_sampler, daemon=True); ct.start()

# --- P0: app start ---
from isaaclab.app import AppLauncher
t = time.time()
launcher = AppLauncher(headless=True, offscreen_render=True, experience='isaacsim.exp.base.kit')
simulation_app = launcher.app
phases['P0_app_start'] = time.time() - t

import omni.usd, omni.kit.commands, omni.kit.app
import omni.replicator.core as rep
import numpy as np
from pxr import UsdGeom, Gf
import carb

kit = omni.kit.app.get_app()
ctx = omni.usd.get_context()

# --- P1: stage open ---
t = time.time()
ctx.open_stage(os.path.abspath(args.scene))
for _ in range(30):
    kit.update()
phases['P1_stage_open'] = time.time() - t

stage = ctx.get_stage()
W, H = args.res

# --- P2: first frame ---
t = time.time()
cam = stage.DefinePrim('/World/RenderCam', 'Camera')
xf = UsdGeom.Xformable(cam)
xf.ClearXformOpOrder()
t_op = xf.AddTranslateOp(); q_op = xf.AddOrientOp()
t_op.Set(Gf.Vec3d(0, 8, 1.5))
q_op.Set(Gf.Quatf(0.7071, 0.7071, 0, 0))
rp = rep.create.render_product('/World/RenderCam', resolution=(W, H))
rgb_a = rep.AnnotatorRegistry.get_annotator('rgb')
rgb_a.attach([rp])
rep.orchestrator.step(rt_subframes=1)
kit.update()
data = rgb_a.get_data()
arr = np.ascontiguousarray(data[..., :3], dtype=np.uint8)
phases['P2_first_frame'] = time.time() - t
phases['P2_first_frame_valid'] = bool(arr.mean() > 5)

# --- P3: render loop ---
t = time.time()
frame_times = []
for i in range(args.frames):
    ft = time.time()
    t_op.Set(Gf.Vec3d(8 * np.cos(i * 0.05), 8 * np.sin(i * 0.05), 1.5))
    rep.orchestrator.step(rt_subframes=1)
    kit.update()
    d = rgb_a.get_data()
    a = np.ascontiguousarray(d[..., :3], dtype=np.uint8)
    frame_times.append(time.time() - ft)
loop = time.time() - t
phases['P3_render_loop_total'] = loop
phases['P3_fps'] = args.frames / loop
frame_times.sort()
phases['P3_frame_p50ms'] = frame_times[len(frame_times)//2] * 1000
phases['P3_frame_p90ms'] = frame_times[int(len(frame_times)*0.9)] * 1000

# --- breakdown within a frame: update vs annotator copy ---
t = time.time()
for i in range(20):
    rep.orchestrator.step(rt_subframes=1)
t_step = (time.time() - t) / 20
t = time.time()
for i in range(20):
    kit.update()
t_upd = (time.time() - t) / 20
t = time.time()
for i in range(20):
    d = rgb_a.get_data()
    a = np.ascontiguousarray(d[..., :3], dtype=np.uint8)
t_copy = (time.time() - t) / 20
phases['P3_step_ms'] = t_step * 1000
phases['P3_kitupdate_ms'] = t_upd * 1000
phases['P3_copy_ms'] = t_copy * 1000

phases['total_wall'] = time.time() - T0
time.sleep(0.5)
stop_flag.set()
time.sleep(0.3)

# samplers summary
import statistics
if gpu_samples:
    g = gpu_samples[3:]  # skip first samples
    phases['gpu_util_p50'] = statistics.median(s['gpu%'] for s in g)
    phases['gpu_util_max'] = max(s['gpu%'] for s in g)
    phases['gpu_mem_mb_p50'] = statistics.median(s['mem_mb'] for s in g)
    phases['gpu_watt_p50'] = statistics.median(float(s['watt']) for s in g if s['watt'] != '[N/A]')
if cpu_samples:
    c = cpu_samples[3:]
    phases['cpu_p50'] = statistics.median(s['cpu%'] for s in c)
    phases['cpu_max'] = max(s['cpu%'] for s in c)
    phases['rss_gb_p50'] = statistics.median(s['rss_gb'] for s in c)
    # busiest cores during render loop
    loop_samples = [s for s in c if s['t'] > phases['P0_app_start'] + phases['P1_stage_open'] + phases['P2_first_frame']]
    if loop_samples:
        ncore = len(loop_samples[0]['cores'])
        core_busy = [statistics.median(s['cores'][i] for s in loop_samples) for i in range(ncore)]
        phases['core_busy_render_p50'] = [round(x) for x in core_busy]

with open(args.out, 'w') as f:
    json.dump({'phases': phases, 'gpu_samples': gpu_samples, 'cpu_samples': cpu_samples[-50:]}, f, indent=1)

print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in phases.items() if not k.startswith('core_')}, indent=1))
simulation_app.close()
