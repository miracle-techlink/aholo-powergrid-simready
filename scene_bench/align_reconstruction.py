"""Fit new SfM to an older reconstruction using shared observed camera poses.

The fit transfers the old frame; it does not certify metric scale or geometry.
Matrices use column vectors. USD Gf callers must transpose the output matrix.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np


def fit_sim3(source, target):
    source, target = np.asarray(source), np.asarray(target)
    x, y = source - source.mean(0), target - target.mean(0)
    if len(x) < 3 or np.linalg.matrix_rank(x, tol=1e-8) < 2:
        raise ValueError('At least three noncollinear shared camera centers required')
    u, singular, vt = np.linalg.svd(y.T @ x / len(x))
    sign = np.ones(3)
    sign[-1] = np.linalg.det(u @ vt)
    rotation = (u * sign) @ vt
    scale = float((singular * sign).sum() / np.square(x).sum(axis=1).mean())
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('Invalid positive scale')
    translation = target.mean(0) - scale * rotation @ source.mean(0)
    return scale, rotation, translation


def residuals(fit, source, target):
    s, r, t = fit
    return np.linalg.norm(s * np.asarray(source) @ r.T + t - target, axis=1)


def robust_fit(source, target, threshold):
    if threshold <= 0:
        raise ValueError('Positive center residual threshold required')
    triples = list(itertools.combinations(range(len(source)), 3))
    if len(triples) > 2000:
        rng = np.random.default_rng(20261108)
        triples = [triples[i] for i in rng.choice(len(triples), 2000, replace=False)]
    best = None
    for ids in triples:
        try:
            fit = fit_sim3(source[list(ids)], target[list(ids)])
        except ValueError:
            continue
        error = residuals(fit, source, target)
        keep = error <= threshold
        score = (int(keep.sum()), -float(np.median(error)))
        if keep.sum() >= 3 and (best is None or score > best[0]):
            best = score, keep
    if best is None:
        raise ValueError('No nondegenerate camera registration consensus')
    keep = best[1]
    for _ in range(5):
        fit = fit_sim3(source[keep], target[keep])
        new_keep = residuals(fit, source, target) <= threshold
        if np.array_equal(keep, new_keep) or new_keep.sum() < 3:
            break
        keep = new_keep
    fit = fit_sim3(source[keep], target[keep])
    return fit, keep


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--new', type=Path, required=True)
    ap.add_argument('--old', type=Path, required=True)
    ap.add_argument('--old-to-scene', type=Path, required=True)
    ap.add_argument('--center-threshold', type=float, default=.15,
                    help='RANSAC threshold in OLD SfM units, not physical meters')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    new_data, old_data = [json.loads(p.read_text()) for p in (args.new, args.old)]
    new = {p['frame_index']: p for p in new_data['poses']}
    old = {p['frame_index']: p for p in old_data['poses']}
    common = sorted(new.keys() & old.keys())
    if len(common) < 6:
        raise ValueError('Require six shared observations for diagnostic registration')
    ns = np.array([new[k]['camera_to_world'] for k in common])
    os = np.array([old[k]['camera_to_world'] for k in common])
    if not np.isfinite(ns).all() or not np.isfinite(os).all():
        raise ValueError('Nonfinite camera pose')
    fit, keep = robust_fit(ns[:, :3, 3], os[:, :3, 3], args.center_threshold)
    s, r, t = fit
    errors = residuals(fit, ns[:, :3, 3], os[:, :3, 3])
    old_scene = json.loads(args.old_to_scene.read_text())
    old_matrix = np.asarray(old_scene['position_transform_Sim3'])
    new_old = np.eye(4)
    new_old[:3, :3], new_old[:3, 3] = s * r, t
    matrix = old_matrix @ new_old
    rows = []
    for i, frame in enumerate(common):
        delta = (r @ ns[i, :3, :3]) @ os[i, :3, :3].T
        angle = float(np.degrees(np.arccos(np.clip((np.trace(delta)-1)/2, -1, 1))))
        rows.append({'frame_index': frame, 'center_error_old_sfm_units': float(errors[i]),
                     'rotation_error_deg': angle, 'inlier': bool(keep[i])})
    report = {'status': 'CANDIDATE_ALIGNMENT_REQUIRES_RENDER_REVIEW',
              'metric_scale_verified': False, 'old_alignment_status': old_scene['status'],
              'new_trajectory': str(args.new.resolve()), 'new_sha256': sha(args.new),
              'old_trajectory': str(args.old.resolve()), 'old_sha256': sha(args.old),
              'old_to_scene': str(args.old_to_scene.resolve()),
              'old_to_scene_sha256': sha(args.old_to_scene),
              'shared_cameras': len(common), 'inliers': int(keep.sum()),
              'center_threshold_old_sfm_units': args.center_threshold,
              'new_to_old_scale': s, 'new_to_old_sim3_column': new_old.tolist(),
              'new_to_scene_sim3_column': matrix.tolist(),
              'usd_gf_row_matrix': matrix.T.tolist(), 'camera_residuals': rows,
              'limitations': ['This inherits the old scene registration uncertainty.',
                              'Camera agreement does not certify geometry or physical scale.',
                              'Apply as parent transform so geometry and SH axes move together.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'shared_cameras': len(common), 'inliers': int(keep.sum()),
                      'max_inlier_center_error': float(errors[keep].max())}))


if __name__ == '__main__':
    main()
