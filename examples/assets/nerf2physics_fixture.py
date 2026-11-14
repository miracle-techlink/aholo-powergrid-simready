"""Create synthetic adapter inputs, explicitly unsuitable for accuracy claims."""

import argparse
from pathlib import Path

import numpy as np

from simready.core.artifacts import write_json
from simready.core.evidence import reference


def create_request(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    features = directory / "features.npz"
    np.savez(
        features,
        patch_features=np.array(
            [[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]]
        ),
        visible=np.array([[True, True, False], [True, True, False]]),
        text_features=np.eye(2),
        material_names=np.array(["steel", "foam"]),
        source_points=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        query_points=np.array([[0.01, 0.0, 0.0], [0.99, 0.0, 0.0]]),
    )
    scale = directory / "synthetic-scale.json"
    write_json(
        scale,
        {
            "kind": "synthetic_fixture",
            "coordinate_unit": "m",
            "voxel_volume_m3": 0.00001,
            "measured": False,
        },
    )
    association = directory / "association.json"
    write_json(
        association,
        {
            "target_object_id": "fixture-cup",
            "source_object_id": "synthetic-object",
            "kind": "synthetic_fixture",
        },
    )
    payload = directory / "features.json"
    write_json(
        payload,
        {
            "features": reference(features),
            "temperature": 0.1,
            "materials": [
                {"name": "steel", "interval": [7800.0, 8000.0], "unit": "kg/m^3"},
                {"name": "foam", "interval": [20.0, 40.0], "unit": "kg/m^3"},
            ],
            "volume_method": "carving",
            "volume_evidence": reference(scale),
            "voxel_volume_m3": 0.00001,
            "correction_factor": 0.6,
        },
    )
    request = directory / "request.json"
    write_json(
        request,
        {
            "schema_version": 1,
            "object_id": "fixture-cup",
            "execution_mode": "synthetic_fixture",
            "property_name": "mass",
            "association": {
                "source_object_id": "synthetic-object",
                "reason": "Synthetic contract test; not a real visual association",
                "evidence": reference(association),
            },
            "payload": reference(payload),
            "scale_evidence": reference(scale),
            "upstream": {
                "repository": "https://github.com/ajzhai/NeRF2Physics",
                "commit": None,
                "license": "MIT",
            },
            "model_identity": {
                "clip_backbone": "synthetic-2d-not-CLIP",
                "checkpoint": "synthetic",
                "weights_sha256": None,
            },
        },
    )
    return request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(create_request(parser.parse_args().out))
