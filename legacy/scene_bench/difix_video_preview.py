"""Locally enhance an existing rendered clip; outputs are generated video, not repaired 3D."""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[2]
METHOD = ROOT / "runs/starbucks_tour/scene_rsi_v1/methods/difix3d"
SOURCE = ROOT / "runs/starbucks_tour/scene_rsi_v2/demo_3s/frames"
OUT = ROOT / "runs/starbucks_tour/scene_rsi_v2/difix_video_3s"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(data):
    temp = OUT / "progress.tmp"
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(OUT / "progress.json")


def main():
    OUT.mkdir(exist_ok=False)
    (OUT / "frames").mkdir()
    files = sorted(SOURCE.glob("rgb_*.png"))
    if len(files) != 90:
        raise ValueError("Expected exactly 90 original rendered frames")
    report = {
        "status": "starting",
        "output_kind": "generative_2d_video_preview_not_3d_repair",
        "source": str(SOURCE),
        "resolution": [1280, 720],
        "fps": 30,
        "duration_s": 3,
        "model": "nvidia/difix",
        "revision": "2b0c6fb5797c26b01154dfdeb19d36e5e2eaf388",
        "steps": 1,
        "timestep": 199,
        "guidance": 0,
        "seed_per_frame": 20261108,
        "cloud_generation_calls": 0,
        "temporal_model": False,
        "frames": [],
    }
    save(report)
    start = time.monotonic()
    try:
        with open("/tmp/g2_starbucks_tour.gpu.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            import torch
            from PIL import Image
            from diffusers.models.attention_processor import AttnProcessor2_0

            torch.set_num_threads(2)
            sys.path.insert(0, str(METHOD / "repo/src"))
            from pipeline_difix import DifixPipeline

            spec = importlib.util.spec_from_file_location(
                "autoencoder_kl", METHOD / "weights/vae/autoencoder_kl.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            vae = module.AutoencoderKL.from_pretrained(
                str(METHOD / "weights/vae"),
                torch_dtype=torch.float16,
                local_files_only=True,
            )
            pipe = DifixPipeline.from_pretrained(
                str(METHOD / "weights"),
                vae=vae,
                torch_dtype=torch.float16,
                local_files_only=True,
            ).to("cuda")
            report["attention"] = {}
            for name, model in [("unet", pipe.unet), ("vae", pipe.vae)]:
                kinds = sorted(
                    {type(p).__name__ for p in model.attn_processors.values()}
                )
                changed = all(k in ("AttnProcessor", "AttnProcessor2_0") for k in kinds)
                if changed:
                    model.set_attn_processor(AttnProcessor2_0())
                report["attention"][name] = {"before": kinds, "sdpa_enabled": changed}
            pipe.set_progress_bar_config(disable=True)
            report["status"] = "enhancing"
            save(report)
            for index, source in enumerate(files):
                source_image = (
                    Image.open(source)
                    .convert("RGB")
                    .resize((1280, 720), Image.Resampling.LANCZOS)
                )
                torch.cuda.synchronize()
                frame_start = time.monotonic()
                with torch.inference_mode():
                    output = pipe(
                        "remove degradation",
                        image=source_image,
                        num_inference_steps=1,
                        timesteps=[199],
                        guidance_scale=0.0,
                        generator=torch.Generator(device="cuda").manual_seed(20261108),
                    ).images[0]
                torch.cuda.synchronize()
                if output.size != (1280, 720):
                    raise ValueError("Unexpected output dimensions")
                path = OUT / "frames" / f"frame_{index:04d}.png"
                output.save(path)
                report["frames"].append(
                    {
                        "index": index,
                        "source_sha256": sha(source),
                        "output_sha256": sha(path),
                        "seconds": time.monotonic() - frame_start,
                    }
                )
                report["elapsed_seconds"] = time.monotonic() - start
                save(report)
                print(
                    f"ENHANCED {index + 1}/90 {report['frames'][-1]['seconds']:.3f}s",
                    flush=True,
                )
            report["status"] = "completed_generated_frames"
            report["peak_cuda_allocated_bytes"] = torch.cuda.max_memory_allocated()
            report["elapsed_seconds"] = time.monotonic() - start
            save(report)
    except BaseException:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
        save(report)
        raise


if __name__ == "__main__":
    main()
