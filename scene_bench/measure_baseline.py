#!/usr/bin/env python3
"""CPU diagnostics on frozen DEV references and GS arrays; never a fidelity score."""
import os
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(key, '1')
import argparse
import datetime
import json
from pathlib import Path
import numpy as np
from PIL import Image
from scene_bench.bench import sha256, evidence_file, candidate_digest

QUANTILES = [0, .01, .05, .5, .95, .99, 1]


def quantiles(values):
    a = np.asarray(values)
    a = a[np.isfinite(a)]
    return {str(q): float(x) for q, x in zip(QUANTILES, np.quantile(a, QUANTILES))} if a.size else None


def image_diagnostics(path):
    with Image.open(path) as image:
        rgb = np.asarray(image.convert('RGB'), dtype=np.float32) / 255
    y = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    c = y[1:-1, 1:-1]
    lap = y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:] - 4*c
    gx, gy = np.diff(y, axis=1), np.diff(y, axis=0)
    h, w = (y.shape[0]//8)*8, (y.shape[1]//8)*8
    blocks = y[:h, :w].reshape(h//8, 8, w//8, 8).transpose(0, 2, 1, 3)
    std = blocks.std(axis=(2, 3))
    return dict(resolution=[y.shape[1], y.shape[0]], luma_mean=float(y.mean()),
        luma_quantiles=quantiles(y[::4, ::4]),
        dark_luma_le_002_fraction=float(np.mean(y <= .02)),
        bright_luma_ge_098_fraction=float(np.mean(y >= .98)),
        any_channel_ge_098_fraction=float(np.mean((rgb >= .98).any(axis=2))),
        laplacian_variance_native_normalized=float(lap.var()),
        mean_absolute_gradient_native=float((np.abs(gx).mean()+np.abs(gy).mean())/2),
        low_information_block_fraction=float(np.mean(std <= .02)),
        low_information_definition='8x8 native-pixel grayscale block std <= 0.02; edge remainder excluded',
        low_information_covered_pixel_fraction=float(h*w/y.size))


def gs_statistics(path):
    from pxr import Usd, UsdGeom, UsdVol
    from scene_bench.audit_usd import local_dependencies
    deps = local_dependencies(Path(path))
    if deps['errors']:
        raise ValueError(str(deps['errors']))
    stage = Usd.Stage.Open(str(path))
    if not stage:
        raise ValueError('Cannot open USD')
    result = dict(stage_meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
                  metric_scale_calibrated=False, local_dependencies=deps['files'], prims=[])
    for prim in stage.Traverse():
        if prim.GetTypeName() != 'ParticleField3DGaussianSplat':
            continue
        field = UsdVol.ParticleField3DGaussianSplat(prim)
        value_o, value_s = field.GetOpacitiesAttr().Get(), field.GetScalesAttr().Get()
        opacity = np.asarray(memoryview(value_o)).reshape(-1)
        scale = np.asarray(memoryview(value_s))
        if scale.ndim != 2 or scale.shape[1] != 3 or len(scale) != len(opacity):
            raise ValueError('Unexpected scale/opacity shapes or mismatch')
        n = len(opacity)
        stride = max(1, (n+199999)//200000)
        counts = {k: 0 for k in ('opacity_nonfinite','opacity_outside_01','opacity_lt_001','opacity_lt_005',
                    'opacity_gt_099','scale_row_nonfinite','scale_row_nonpositive','anisotropy_gt_100',
                    'max_scale_gt_01_stageunits','max_scale_gt_1_stageunits')}
        for start in range(0, n, 65536):
            o, s = opacity[start:start+65536], scale[start:start+65536]
            valid = np.isfinite(s).all(axis=1) & (s > 0).all(axis=1)
            counts['opacity_nonfinite'] += int((~np.isfinite(o)).sum())
            counts['opacity_outside_01'] += int(((o<0)|(o>1)).sum())
            counts['opacity_lt_001'] += int((o<.01).sum())
            counts['opacity_lt_005'] += int((o<.05).sum())
            counts['opacity_gt_099'] += int((o>.99).sum())
            counts['scale_row_nonfinite'] += int((~np.isfinite(s).all(axis=1)).sum())
            counts['scale_row_nonpositive'] += int((s<=0).any(axis=1).sum())
            safe = s[valid]
            counts['anisotropy_gt_100'] += int((safe.max(axis=1)/safe.min(axis=1)>100).sum())
            counts['max_scale_gt_01_stageunits'] += int((safe.max(axis=1)>.1).sum())
            counts['max_scale_gt_1_stageunits'] += int((safe.max(axis=1)>1).sum())
        sample = scale[::stride]
        valid = np.isfinite(sample).all(axis=1) & (sample>0).all(axis=1)
        safe = sample[valid]
        result['prims'].append(dict(path=str(prim.GetPath()), count=n,
            count_denominator='all Gaussian rows; invalid values separately counted', counts=counts,
            fractions={k:v/n if n else None for k,v in counts.items()},
            quantile_sampling=dict(method='deterministic evenly strided rows; approximate quantiles', stride=stride, rows=len(sample)),
            opacity_quantiles=quantiles(opacity[::stride]),
            local_max_scale_stageunits_quantiles=quantiles(safe.max(axis=1)),
            local_min_scale_stageunits_quantiles=quantiles(safe.min(axis=1)),
            anisotropy_quantiles=quantiles(safe.max(axis=1)/safe.min(axis=1)),
            nonidentity_prim_transform=not np.allclose(np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim)), np.eye(4)),
            limits='Local Gaussian scale parameters, not world surface sizes or calibrated metres; thin splats, high anisotropy and low opacity can be legitimate. Thresholds are descriptive, not removal rules.'))
    return result


def run(config_path, out):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    root = config_path.parent
    manifest_path = root/cfg['reference_manifest']
    if sha256(manifest_path) != cfg['reference_manifest_sha256']:
        raise ValueError('Frozen manifest mismatch')
    manifest = json.loads(manifest_path.read_text())
    report = dict(schema_version=1, created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        script_sha256=sha256(__file__), config_sha256=sha256(config_path),
        reference_manifest_sha256=sha256(manifest_path),
        cloud_calls=0, gpu_used=False, heldout_images_read=False, overall_fidelity_score=None,
        image_metric_scope='Native-resolution sRGB-derived luma diagnostics, not linear radiometry or perceptual fidelity. Content and exposure affect sharpness; do not optimize these alone.',
        dev_frames=[], candidates=[], camera_alignment_status='NOT_ALIGNED_NO_MATCHED_RENDER')
    for f in manifest['frames']:
        if f['split'] != 'dev':
            continue
        path = evidence_file({'path':f['filename'], 'sha256':f['sha256']}, manifest_path.parent)
        report['dev_frames'].append(dict(id=f['id'], sha256=f['sha256'], **image_diagnostics(path)))
    metric_names = ['luma_mean','dark_luma_le_002_fraction','bright_luma_ge_098_fraction',
        'any_channel_ge_098_fraction','laplacian_variance_native_normalized',
        'mean_absolute_gradient_native','low_information_block_fraction']
    report['dev_summary'] = {k: dict(min=min(f[k] for f in report['dev_frames']),
        median=float(np.median([f[k] for f in report['dev_frames']])),
        max=max(f[k] for f in report['dev_frames'])) for k in metric_names}
    for candidate in cfg['candidates']:
        for asset in candidate['assets']:
            evidence_file(asset, root)
        data = gs_statistics(root/candidate['assets'][0]['path'])
        report['candidates'].append(dict(id=candidate['id'], candidate_digest=candidate_digest(candidate), **data))
    report['alignment_acceptance_requirements'] = dict(
        optimization_split='dev only; guard/heldout not opened or tuned',
        provenance='Freeze input frame, all candidate dependencies, intrinsics/distortion/extrinsics, renderer settings and output hashes.',
        correspondence='At least six static, semantically identified anchors spread across the image and multiple depth layers; separate fit and validation anchors; do not reuse fitting residuals as validation.',
        reported_residuals='Per-anchor pixel error plus median/p95 normalized by image diagonal on independent validation anchors; record visibility and uncertainty.',
        masks='Freeze shared masks before candidate comparison; exclude moving people, strong reflections and genuinely non-common visible areas; report coverage and exclusions.',
        decision='Independent reviewer must inspect overlay and wall/door/table geometry. No universal numeric threshold established; NOT_ALIGNED until evidence supports correspondence. Rendered-room plausibility alone does not establish camera matching.',
        metrics='Only after correspondence approval compute masked matched-view photometric diagnostics. No PSNR on arbitrary views. Never a standalone geometric-fidelity score.')
    out.mkdir(parents=True, exist_ok=True)
    destination = out/'baseline_measurements.json'
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    temporary.replace(destination)
    print(json.dumps(dict(report=str(destination), dev_frames=len(report['dev_frames']), candidates=[dict(id=c['id'], points=sum(p['count'] for p in c['prims'])) for c in report['candidates']]), ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('runs/starbucks_tour/scene_bench_v1/benchmark.json'))
    parser.add_argument('--out', type=Path, default=Path('runs/starbucks_tour/scene_bench_v1/measurement'))
    args=parser.parse_args()
    run(args.config,args.out)
