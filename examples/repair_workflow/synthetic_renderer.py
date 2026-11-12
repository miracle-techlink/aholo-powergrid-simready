"""Synthetic calibration worker: change a model parameter, then render it.

This is deliberately not a 3D reconstruction plugin. It never copies or reads
the reference image; the verifier alone compares the rendered model to it.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from simready.core.artifacts import read_json, write_json
from simready.core.evidence import reference, resolve_ref


def render_model(model, path):
    pixels = np.random.default_rng(model["seed"]).integers(
        30, 220, (64, 64, 3), dtype=np.uint8
    )
    image = np.clip(pixels.astype(float) * model["gain"], 0, 255).astype(np.uint8)
    Image.fromarray(image).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    candidate = read_json(args.candidate)
    model = read_json(resolve_ref(candidate["artifacts"][0], args.candidate.parent))
    model["gain"] = read_json(args.parameters)["parameters"]["gain"]
    write_json(args.out / "model.json", model)
    render_model(model, args.out / "render.png")
    candidate.update(
        candidate_id=candidate["candidate_id"] + "-repaired",
        artifacts=[dict(artifact_id="model", **reference(args.out / "model.json"))],
        views=[dict(view_id="front", rgb=reference(args.out / "render.png"))],
    )
    write_json(args.out / "candidate.json", candidate)


if __name__ == "__main__":
    main()
