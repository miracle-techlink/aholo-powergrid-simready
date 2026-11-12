#!/usr/bin/env python3
"""Freeze original-video references, using CPU decoding only.

16 uniform time-bin midpoints: first 10 dev, next 2 guard, final 4 heldout.
The guard interval reduces adjacent-frame leakage. This is not independently
calibrated camera/geometry GT or an unseen reconstruction-training test set.
Existing manifests and images are verified, never regenerated or overwritten.
Use a new output/version if source, sampling policy, or this script changes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

PIPELINE = Path("/home/nvidia/graduation_material/hackathon_pipeline")
REPO = Path("/home/nvidia/algotithm")
POLICY = {
    "count": 16,
    "sampling": "nearest decoded PTS to each of 16 equal-duration bin midpoints",
    "split_by_sample_index": ["dev"] * 10 + ["guard"] * 2 + ["heldout"] * 4,
    "split_segments_fraction": {
        "dev": [0, 0.625],
        "guard": [0.625, 0.75],
        "heldout": [0.75, 1],
    },
    "guard_used_for_optimization_or_scoring": False,
    "rotation": "ffmpeg default autorotate, honoring original display matrix",
    "resize": None,
    "output_format": "lossless PNG, RGB",
    "decoder_threads": 1,
    "filter_threads": 1,
    "encoder_threads": 1,
    "hardware_acceleration": "none",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def call(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)


def inspect_video(path: Path) -> dict:
    info = json.loads(
        call(
            [
                "ffprobe",
                "-v",
                "error",
                "-threads",
                "1",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration,start_time,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=duration,size",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    stream = info["streams"][0]
    rotation = next(
        (x["rotation"] for x in stream.get("side_data_list", []) if "rotation" in x),
        stream.get("tags", {}).get("rotate", 0),
    )
    stream_hash = (
        call(
            [
                "ffmpeg",
                "-v",
                "error",
                "-threads",
                "1",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ]
        )
        .strip()
        .split("=", 1)[1]
    )
    return dict(
        path=str(path.resolve()),
        sha256=sha256(path),
        bytes=path.stat().st_size,
        encoded_video_payload_sha256=stream_hash,
        probe=info,
        duration_s=float(stream.get("duration") or info["format"]["duration"]),
        coded_resolution=[stream["width"], stream["height"]],
        rotation_degrees=float(rotation),
    )


def calibration_inventory() -> dict:
    roots = [
        PIPELINE / "assets",
        PIPELINE / "runs",
        PIPELINE / "scripts",
        REPO / "runs/starbucks_tour",
    ]
    patterns = [
        "cameras.bin",
        "cameras.txt",
        "images.bin",
        "images.txt",
        "points3D.bin",
        "transforms*.json",
        "*calibrat*.json",
        "*intrinsic*.json",
        "*extrinsic*.json",
        "*camera*.json",
    ]
    command = ["rg", "--files", *map(str, roots)]
    for pattern in patterns:
        command += ["-g", pattern]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Calibration file inventory failed: " + result.stderr)
    found = sorted(result.stdout.splitlines())
    authored = [
        p
        for p in found
        if "/rotation_queue/camera.json" in p or "/preview_queue/camera.json" in p
    ]
    unrelated = [
        p for p in found if "/benchmark_2610_04432/" in p or "/local_spatialgen/" in p
    ]
    candidates = [p for p in found if p not in authored and p not in unrelated]
    return dict(
        search_roots=list(map(str, roots)),
        filename_patterns=patterns,
        original_video_calibration_verified=False,
        colmap_camera_model_found=any(
            Path(p).name in ("cameras.bin", "cameras.txt") for p in candidates
        ),
        candidates_for_manual_review=candidates,
        authored_simulation_camera_files=authored,
        excluded_code_or_benchmark_paths=unrelated,
        observations=[
            "No original Starbucks camera intrinsics/extrinsics or COLMAP model was verified in these project roots.",
            "Existing rotation_queue/camera.json is an authored simulator eye pose from Gaussian-floor fitting, not phone-camera calibration.",
            "runs/scene/lod-meta.json contains Gaussian LOD metadata, not camera calibration; scene_high_task/scene_gen_task JSON contain worldId only.",
            "Other repository RoboTwin/RoboDojo calibration files belong to unrelated simulator episodes and cannot calibrate this video.",
        ],
    )


def contact_sheet(frames: list[dict], directory: Path) -> Path:
    canvas = Image.new("RGB", (800, 1324), (25, 28, 33))
    draw = ImageDraw.Draw(canvas)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font = (
        ImageFont.truetype(font_path, 13)
        if Path(font_path).exists()
        else ImageFont.load_default()
    )
    draw.text(
        (12, 8),
        "Original Starbucks video | 10 dev / 2 guard / 4 heldout | NOT camera GT",
        font=font,
        fill="white",
    )
    colors = {
        "dev": (116, 202, 158),
        "guard": (245, 188, 84),
        "heldout": (184, 162, 255),
    }
    for i, row in enumerate(frames):
        x, y = (i % 4) * 200, 36 + (i // 4) * 322
        with Image.open(directory / row["filename"]) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((188, 280), Image.Resampling.LANCZOS)
            canvas.paste(thumb, (x + (200 - thumb.width) // 2, y))
        draw.text(
            (x + 7, y + 283),
            f"{row['id']} | {row['timestamp_s']:.3f}s",
            font=font,
            fill="white",
        )
        draw.text((x + 7, y + 302), row["split"], font=font, fill=colors[row["split"]])
    out = directory / "contact_sheet.jpg"
    canvas.save(out, quality=92, subsampling=0)
    return out


def verify_existing(out: Path, source: Path, alternate: Path, version: str) -> dict:
    manifest = json.loads((out / "manifest.json").read_text())
    required = [
        (manifest["version"] == version, "version"),
        (manifest["sampling_policy"] == POLICY, "sampling policy"),
        (
            manifest["extractor"]["script_sha256"] == sha256(Path(__file__)),
            "extractor source",
        ),
        (manifest["source"]["sha256"] == sha256(source), "source bytes"),
        (
            manifest["alternate_source"]["sha256"] == sha256(alternate),
            "alternate source bytes",
        ),
    ]
    for valid, name in required:
        if not valid:
            raise ValueError(
                f"Frozen {name} changed: choose a new version/output directory"
            )
    for record in manifest["frames"] + [manifest["contact_sheet"]]:
        path = out / record["filename"]
        if sha256(path) != record["sha256"]:
            raise ValueError(
                f"Frozen artifact changed: {path}; do not overwrite this reference set"
            )
        with Image.open(path) as image:
            image.verify()
    return manifest


def prepare(source: Path, alternate: Path, out: Path, version: str) -> dict:
    if (out / "manifest.json").exists():
        manifest = verify_existing(out, source, alternate, version)
        print(
            json.dumps(
                {
                    "state": "verified_frozen_unchanged",
                    "manifest": str(out / "manifest.json"),
                    "manifest_sha256": sha256(out / "manifest.json"),
                }
            )
        )
        return manifest
    if out.exists():
        raise FileExistsError(
            f"{out} exists without a frozen manifest; choose a fresh directory"
        )
    info, alternate_info = inspect_video(source), inspect_video(alternate)
    decoded = json.loads(
        call(
            [
                "ffprobe",
                "-v",
                "error",
                "-threads",
                "1",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(source),
            ]
        )
    )["frames"]
    pts = [float(frame["best_effort_timestamp_time"]) for frame in decoded]
    if not pts or any(b < a for a, b in zip(pts, pts[1:])):
        raise ValueError("Missing or non-monotonic source frame timestamps")
    targets = [
        (i + 0.5) * info["duration_s"] / POLICY["count"] for i in range(POLICY["count"])
    ]
    selected = [
        min(range(len(pts)), key=lambda n: abs(pts[n] - pts[0] - target))
        for target in targets
    ]
    if len(set(selected)) != 16:
        raise ValueError("Source too short to provide 16 distinct frames")
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=out.name + ".preparing.", dir=out.parent))
    try:
        (temporary / "frames").mkdir()
        expression = "+".join(f"eq(n\\,{i})" for i in selected)
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-threads",
            "1",
            "-hwaccel",
            "none",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-filter_threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-vf",
            "select=" + expression,
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "16",
            "-c:v",
            "png",
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-start_number",
            "0",
            str(temporary / "frames/ref_%04d.png"),
        ]
        subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        images = sorted((temporary / "frames").glob("ref_*.png"))
        if len(images) != 16:
            raise ValueError(f"Expected 16 output images, found {len(images)}")
        records = []
        for i, (path, index) in enumerate(zip(images, selected)):
            with Image.open(path) as image:
                image.load()
                resolution = list(image.size)
            records.append(
                dict(
                    id=f"ref_{i:04d}",
                    filename=str(path.relative_to(temporary)),
                    split=POLICY["split_by_sample_index"][i],
                    frame_index=index,
                    target_timestamp_s=targets[i],
                    timestamp_s=pts[index] - pts[0],
                    original_pts_s=pts[index],
                    timestamp_error_s=pts[index] - pts[0] - targets[i],
                    resolution=resolution,
                    sha256=sha256(path),
                )
            )
        sheet = contact_sheet(records, temporary)
        duration = info["duration_s"]
        manifest = dict(
            schema="starbucks-reference-set/1",
            version=version,
            created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            frozen=True,
            source=info,
            alternate_source=alternate_info,
            alternate_relationship=dict(
                file_bytes_identical=info["sha256"] == alternate_info["sha256"],
                encoded_video_payload_identical=info["encoded_video_payload_sha256"]
                == alternate_info["encoded_video_payload_sha256"],
                independent_holdout_source=False,
                note="Matching encoded video payload means v2 is not a separate camera recording.",
            ),
            sampling_policy=POLICY,
            split_time_segments_s={
                name: [a * duration, b * duration]
                for name, (a, b) in POLICY["split_segments_fraction"].items()
            },
            decoded_frame_count=len(decoded),
            source_first_pts_s=pts[0],
            orientation=dict(
                coded_resolution=info["coded_resolution"],
                source_rotation_degrees=info["rotation_degrees"],
                autorotate_applied=True,
                displayed_resolution=records[0]["resolution"],
            ),
            frames=records,
            contact_sheet=dict(filename="contact_sheet.jpg", sha256=sha256(sheet)),
            calibration_inventory=calibration_inventory(),
            limitations=[
                "These frames are image references, not independent calibrated camera or 3D geometry ground truth.",
                "A contiguous temporal split and guard reduce neighboring-frame leakage but do not guarantee independent scene content.",
                "If Aholo/reconstruction consumed the full original clip, heldout frames were already available to that reconstruction; this split only freezes subsequent tuning/evaluation use.",
                "The contact sheet includes heldout thumbnails for audit; do not feed it or heldout PNGs to an optimization agent.",
                "Without matched camera poses/intrinsics, arbitrary render-vs-frame pixel errors are not valid reconstruction fidelity scores.",
            ],
            extractor=dict(
                script=str(Path(__file__).resolve()),
                script_sha256=sha256(Path(__file__)),
                ffmpeg_version=call(["ffmpeg", "-version"]).splitlines()[0],
                ffprobe_version=call(["ffprobe", "-version"]).splitlines()[0],
                command_template=[
                    str(value).replace(str(temporary), "<OUTPUT>") for value in command
                ],
                gpu_used=False,
            ),
        )
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        temporary.rename(out)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(
        json.dumps(
            {
                "state": "frozen_created",
                "manifest": str(out / "manifest.json"),
                "manifest_sha256": sha256(out / "manifest.json"),
                "count": 16,
                "splits": {"dev": 10, "guard": 2, "heldout": 4},
                "resolution": records[0]["resolution"],
            }
        )
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=PIPELINE / "assets/starbucks.mp4"
    )
    parser.add_argument(
        "--alternate", type=Path, default=PIPELINE / "assets/starbucks_v2.mp4"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "runs/starbucks_tour/scene_bench_v1/references",
    )
    parser.add_argument("--version", default="scene_bench_v1")
    args = parser.parse_args()
    prepare(
        args.source.resolve(),
        args.alternate.resolve(),
        args.out.resolve(),
        args.version,
    )


if __name__ == "__main__":
    main()
