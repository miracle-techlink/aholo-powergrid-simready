"""CPU-only independent composition audit for bounded local Gaussian patches."""
import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Sdf, Usd, UsdGeom

from scene_bench.bench import evidence_file, sha256


def audit(folder):
    folder = Path(folder).resolve()
    coverage = json.loads((folder / 'coverage.json').read_text())
    support_path = evidence_file(coverage['support_file'], folder)
    support = np.load(support_path)['support']
    for item in coverage['depth_evidence']:
        evidence_file(item, folder)
    results = []
    for file in sorted(folder.glob('*/candidate_manifest.json')):
        data = json.loads(file.read_text())
        for item in data['source_assets']:
            evidence_file(item, folder)
        evidence_file({'path': data['scene'], 'sha256': data['scene_sha256']}, folder)
        evidence_file({'path': data['patch'], 'sha256': data['patch_sha256']}, folder)
        evidence_file(data['coverage'], folder)
        evidence_file(data['proposal'], folder)
        source = Usd.Stage.Open(data['source_assets'][0]['path'])
        candidate = Usd.Stage.Open(data['scene'])
        a, b = (s.GetPrimAtPath('/World/StarbucksSplats') for s in (source, candidate))
        attrs = []
        layer = Sdf.Layer.FindOrOpen(data['patch'])
        def visit(path):
            if isinstance(layer.GetObjectAtPath(path), Sdf.AttributeSpec):
                attrs.append(str(path))
        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
        if set(attrs) != {'/World/StarbucksSplats.scales', '/World/StarbucksSplats.opacities'}:
            raise ValueError('Patch authors unexpected attributes')
        if list(candidate.GetRootLayer().subLayerPaths) != [data['patch'], data['source_assets'][0]['path']]:
            raise ValueError('Unexpected scene layering')
        for name in ('upAxis', 'metersPerUnit'):
            if source.GetMetadata(name) != candidate.GetMetadata(name):
                raise ValueError('Coordinate contract changed')
        if not np.array_equal(np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(a)),
                              np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(b))):
            raise ValueError('World transform changed')
        for attr in a.GetAttributes():
            if attr.GetName() not in ('scales', 'opacities'):
                if not np.array_equal(np.asarray(attr.Get()), np.asarray(b.GetAttribute(attr.GetName()).Get())):
                    raise ValueError('Unexpected composed change: ' + attr.GetName())
        s0, s1 = (np.asarray(p.GetAttribute('scales').Get()) for p in (a, b))
        o0, o1 = (np.asarray(p.GetAttribute('opacities').Get()) for p in (a, b))
        if len(support) != len(s0) or s1.shape != s0.shape or o1.shape != o0.shape:
            raise ValueError('Point layout changed')
        if not np.array_equal(s0[~support], s1[~support]) or not np.array_equal(o0[~support], o1[~support]):
            raise ValueError('Unsupported points changed')
        ratios = s1.astype(float) / s0.astype(float)
        if not np.isfinite(s1).all() or not np.isfinite(o1).all() or (s1 <= 0).any():
            raise ValueError('Invalid numeric data')
        if ratios.min() < 1 - 1e-6 or ratios.max() > data['standard_deviation_ratio_cap'] + 1e-6 or (o1 > o0).any() or (o1 < 0).any():
            raise ValueError('Scale or opacity cap violated')
        integral0 = o0.astype(float) * np.prod(s0.astype(float), axis=1)
        integral1 = o1.astype(float) * np.prod(s1.astype(float), axis=1)
        error = np.max(np.abs(integral1 - integral0) / np.maximum(integral0, 1e-30))
        if error > 1e-6:
            raise ValueError('Gaussian integral compensation failed')
        results.append({'candidate_id': data['candidate_id'], 'passed': True,
                        'candidate_manifest_sha256': sha256(file), 'patch_sha256': sha256(data['patch']),
                        'support_sha256': sha256(support_path), 'points_unchanged_outside_support': int((~support).sum()),
                        'max_scale_ratio': float(ratios.max()), 'max_integral_relative_error': float(error),
                        'scope': 'Composition/numeric/locality checks; not independent geometry or occlusion truth'})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.folder)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
