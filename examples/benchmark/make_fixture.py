"""Generate a known synthetic RGB pair to exercise the independent observer."""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from simready_bridge.artifacts import digest, write_json
from simready_bridge.benchmark.contracts import reference

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
root = args.out.resolve()
root.mkdir(parents=True, exist_ok=False)
pixels = np.random.default_rng(12).integers(0, 256, (64, 64, 3), dtype=np.uint8)
for name in ('source', 'render'):
    Image.fromarray(pixels).save(root / f'{name}.png')
(root / 'model.txt').write_text('Synthetic test fixture, not a reconstructed scene.\n')
protocol = dict(schema_version=1, benchmark_id='example-observer-only', profile='observer_calibration',
                phase='development', fit_frame_ids=[], fit_image_sha256=[],
                views=[dict(view_id='front', asset_id='fixture', frame_id='test', split='development',
                            reference_kind='synthetic_gt', rgb=reference(root / 'source.png'), regions=[])],
                checks=[dict(check_id='coverage', track='coverage', metric='coverage.observation_fraction',
                             op='>=', limit=1, required=True, severity='critical', calibration='structural'),
                        dict(check_id='rgb', track='appearance', metric='appearance.psnr_db.mean',
                             op='>=', limit=22, required=True, severity='critical', calibration='structural')])
write_json(root / 'protocol.json', protocol)
write_json(root / 'candidate.json', dict(schema_version=1, candidate_id='synthetic-example',
           protocol_sha256=digest(root / 'protocol.json'),
           artifacts=[dict(artifact_id='fixture', **reference(root / 'model.txt'))],
           views=[dict(view_id='front', rgb=reference(root / 'render.png'))], fit_frame_ids=[], fit_image_sha256=[]))
