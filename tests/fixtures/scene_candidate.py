import numpy as np
from PIL import Image

from simready.core.artifacts import digest, write_json
from simready.core.evidence import reference


def make_scene_candidate(tmp_path):
    rng = np.random.default_rng(12)
    source = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for name in ("source", "render"):
        Image.fromarray(source).save(tmp_path / f"{name}.png")
    (tmp_path / "model.bin").write_bytes(b"candidate-model")

    def check(name, track, metric, limit):
        return dict(
            check_id=name,
            track=track,
            metric=metric,
            op=">=",
            limit=limit,
            required=True,
            severity="critical",
            calibration="structural",
            defect_kind="appearance_mismatch",
        )

    protocol = dict(
        schema_version=1,
        benchmark_id="fault-fixture",
        profile="observer_calibration",
        phase="development",
        fit_frame_ids=["train-frame"],
        fit_image_sha256=[],
        views=[
            dict(
                view_id="front",
                asset_id="cabinet",
                frame_id="test-frame",
                split="development",
                reference_kind="synthetic_gt",
                rgb=reference(tmp_path / "source.png"),
                regions=[dict(asset_id="button", bbox_xyxy=[16, 16, 32, 32])],
            )
        ],
        checks=[
            check("coverage", "coverage", "coverage.observation_fraction", 1),
            check("visual", "appearance", "appearance.psnr_db.mean", 22),
            check("critical_roi", "appearance", "appearance.psnr_db.region_min", 22),
        ],
    )
    p = tmp_path / "protocol.json"
    write_json(p, protocol)
    candidate = dict(
        schema_version=1,
        candidate_id="fixture",
        protocol_sha256=digest(p),
        artifacts=[dict(artifact_id="model", **reference(tmp_path / "model.bin"))],
        views=[dict(view_id="front", rgb=reference(tmp_path / "render.png"))],
        fit_frame_ids=[],
        fit_image_sha256=[],
    )
    c = tmp_path / "candidate.json"
    write_json(c, candidate)
    return p, c, protocol, candidate, source
