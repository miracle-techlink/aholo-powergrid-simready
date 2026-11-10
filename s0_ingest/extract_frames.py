#!/usr/bin/env python3
"""s0: 视频→训练帧. 清晰度过滤(拉普拉斯) + 感知哈希去重 + 曝光统计 + heldout 划分.
输出: frames/train/*.jpg  frames/heldout/*.jpg  manifest.json"""
import subprocess, json, os, sys, hashlib
import numpy as np
from PIL import Image

VIDEO = os.path.expanduser('~/Desktop/场景素材/mv_panel_room_full.mp4')
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'panel_frames')
FPS = 2.0            # 抽帧率
HELDOUT_EVERY = 10   # 每10帧1帧进heldout(过滤后计数)
MIN_LAP = 60.0       # 拉普拉斯方差下限(运动模糊剔除)
PHASH_D = 8          # 感知哈希
DEDUP_HAM = 6        # 汉明距离阈值(与前一张比较)

os.makedirs(f'{OUT}/train', exist_ok=True)
os.makedirs(f'{OUT}/heldout', exist_ok=True)

def phash(im):
    g = im.convert('L').resize((32,32), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float32)
    d = a[1::2,1::2] - a[::2,::2]  # dhash式梯度
    return (d > d.mean()).flatten()

# ffmpeg 抽帧
tmp = f'{OUT}/_raw'
os.makedirs(tmp, exist_ok=True)
subprocess.run(['ffmpeg','-y','-i',VIDEO,'-vf',f'fps={FPS}','-q:v','2',f'{tmp}/f%04d.jpg'],
               check=True, capture_output=True)
files = sorted(os.listdir(tmp))
print(f'raw frames: {len(files)}')

keep, last_h = [], None
for i, fn in enumerate(files):
    im = Image.open(f'{tmp}/{fn}')
    a = np.asarray(im.convert('L'), dtype=np.float32)
    # laplacian variance
    lap = (np.roll(a,1,0)+np.roll(a,-1,0)+np.roll(a,1,1)+np.roll(a,-1,1)-4*a)
    lv = lap[2:-2,2:-2].var()
    if lv < MIN_LAP:
        continue
    h = phash(im)
    if last_h is not None and (np.packbits(h) != np.packbits(last_h)).sum() <= DEDUP_HAM:
        continue
    last_h = h
    keep.append((fn, lv, float(a.mean())))
print(f'after quality+dedup: {len(keep)}')

manifest = {'video': VIDEO, 'fps': FPS, 'min_lap': MIN_LAP, 'frames': []}
n_train = n_hold = 0
for idx, (fn, lv, mean_b) in enumerate(keep):
    split = 'heldout' if idx % HELDOUT_EVERY == (HELDOUT_EVERY//2) else 'train'
    dst = f'{OUT}/{split}/{idx:04d}.jpg'
    os.replace(f'{tmp}/{fn}', dst)
    manifest['frames'].append({'idx': idx, 'split': split, 'laplacian': round(float(lv),1),
                               'mean_brightness': round(float(mean_b),1),
                               'sha256': hashlib.sha256(open(dst,'rb').read()).hexdigest()[:16]})
    if split=='train': n_train+=1
    else: n_hold+=1

import shutil; shutil.rmtree(tmp)
manifest['n_train'], manifest['n_heldout'] = n_train, n_hold
json.dump(manifest, open(f'{OUT}/manifest.json','w'), indent=1)
print(f'DONE train={n_train} heldout={n_hold} -> {OUT}')
