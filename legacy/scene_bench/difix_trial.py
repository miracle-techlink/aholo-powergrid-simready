#!/usr/bin/env python3
"""Isolated official Difix no-reference trial. Does not read reference/GT images.
Run only under an explicit root-agent GPU lease; shared advisory GPU lock is mandatory.
Results are image_only, never a repaired 3D scene.
"""

import argparse, datetime, fcntl, hashlib, importlib.util, json, os, sys, time, traceback, subprocess
from pathlib import Path

BASE = Path("/home/nvidia/algotithm/runs/starbucks_tour/scene_rsi_v1/methods")
MODEL_REV = "2b0c6fb5797c26b01154dfdeb19d36e5e2eaf388"


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def initial():
    for name in ["difix3d", "artifixer3d"]:
        folder = BASE / name
        folder.mkdir(parents=True, exist_ok=True)
        report = {
            "method": name,
            "status": "weights_downloading_cpu_preparation",
            "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "repo": str(folder / "repo"),
            "repo_commit": subprocess.check_output(
                ["git", "-C", str(folder / "repo"), "rev-parse", "HEAD"], text=True
            ).strip(),
            "shared_environment_read_only": True,
            "isolated_venv": str(folder / "venv"),
            "cloud_generation_calls": 0,
            "cloud_api_cost_usd": 0,
            "electricity_cost_usd": None,
            "gpu_inference_run": False,
            "actual_output_kind": None,
            "weights_bytes_on_disk": sum(
                p.stat().st_size for p in (folder / "weights").rglob("*") if p.is_file()
            ),
            "errors": [
                {
                    "phase": "initial_download",
                    "error": "Inherited HF_ENDPOINT=https://hf-mirror.com returned invalid Hub metadata; rerun with explicit official endpoint https://huggingface.co",
                    "recovered": True,
                }
            ],
            "limitations": [
                "No claim of scene repair before actual new Gaussian asset is trained/exported.",
                "Do not use evaluation ref3/ref4 source photographs for conditioning.",
            ],
        }
        if name == "artifixer3d":
            report.update(
                checkpoint="nvidia/ArtiFixer/artifixer-1.3b.pt",
                checkpoint_revision="4d82a1472e8cae0e7b40a96c7fdda6c35cc2f5fc",
                cpu_cli_help="passed",
                cpu_full_import="initially failed: missing threedgrut; pinned official source submodule cloning",
                input_blocker="Needs non-evaluation anchors, calibrated cameras, opacity renders and actual T5 caption embeddings; no fabricated calibration/GT will be used",
                minimal_smoke="Can test model with explicitly synthetic conditioning but cannot count it as scene restoration",
            )
        else:
            report.update(
                model_id="nvidia/difix",
                model_revision=MODEL_REV,
                no_reference_conditioning=True,
                cpu_pipeline_import="passed; custom VAE dependency pins being installed",
            )
        write(folder / "trial.json", report)


def run(args):
    folder = BASE / "difix3d"
    report_path = folder / args.trial_file
    report = json.loads(
        (report_path if report_path.exists() else folder / "trial.json").read_text()
    )
    report.pop("weight_files", None)
    start = time.monotonic()
    report.update(
        status="running",
        command=sys.argv,
        updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        input=str(args.input.resolve()),
        input_sha256=digest(args.input),
        reference_images=[],
        random_seed=20261108,
        requested_steps=1,
        timestep=199,
        output_kind="image_only",
        gpu_lock="/tmp/g2_starbucks_tour.gpu.lock",
    )
    write(report_path, report)
    try:
        with open(report["gpu_lock"], "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            import torch
            from PIL import Image

            torch.set_num_threads(2)
            sys.path.insert(0, str(folder / "repo/src"))
            from pipeline_difix import DifixPipeline

            spec = importlib.util.spec_from_file_location(
                "autoencoder_kl", folder / "weights/vae/autoencoder_kl.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            dtype = torch.float16
            t = time.monotonic()
            vae = module.AutoencoderKL.from_pretrained(
                str(folder / "weights/vae"), torch_dtype=dtype, local_files_only=True
            )
            pipe = DifixPipeline.from_pretrained(
                str(folder / "weights"),
                vae=vae,
                torch_dtype=dtype,
                local_files_only=True,
            ).to("cuda")
            report["model_load_seconds"] = time.monotonic() - t
            source = Image.open(args.input).convert("RGB")
            report["input_resolution"] = list(source.size)
            if args.long_edge and max(source.size) > args.long_edge:
                scale = args.long_edge / max(source.size)
                size = tuple(max(8, int(n * scale) // 8 * 8) for n in source.size)
                source = source.resize(size, Image.Resampling.LANCZOS)
            report["processed_resolution"] = list(source.size)
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
            t = time.monotonic()
            generator = torch.Generator(device="cuda").manual_seed(20261108)
            with torch.inference_mode():
                result = pipe(
                    "remove degradation",
                    image=source,
                    num_inference_steps=1,
                    timesteps=[199],
                    guidance_scale=0.0,
                    generator=generator,
                ).images[0]
            torch.cuda.synchronize()
            report["inference_seconds"] = time.monotonic() - t
            report.update(
                peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                torch=torch.__version__,
                cuda_build=torch.version.cuda,
                gpu_name=torch.cuda.get_device_name(0),
                gpu_inference_run=True,
            )
            target = folder / args.output
            target.parent.mkdir(parents=True, exist_ok=True)
            result.save(target)
            report.update(
                status="completed_image_only",
                output=str(target),
                output_sha256=digest(target),
                output_resolution=list(result.size),
                elapsed_seconds=time.monotonic() - start,
                actual_output_kind="image_only",
                original_scene_modified=False,
            )
            for name in [
                "text_encoder/model.safetensors",
                "unet/diffusion_pytorch_model.safetensors",
                "vae/diffusion_pytorch_model.safetensors",
            ]:
                report.setdefault("weight_files", []).append(
                    {
                        "path": name,
                        "bytes": (folder / "weights" / name).stat().st_size,
                        "sha256": digest(folder / "weights" / name),
                    }
                )
            write(report_path, report)
    except BaseException as exc:
        report.update(
            status="failed",
            actual_error=str(exc),
            traceback=traceback.format_exc(),
            elapsed_seconds=time.monotonic() - start,
        )
        write(report_path, report)
        raise
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ["traceback", "weight_files"]}
        )
    )


def artifixer_inputs():
    """Synthetic deployment dataset; never score these outputs as scene fidelity."""
    import numpy as np, h5py
    from PIL import Image

    folder = BASE / "artifixer3d"
    out = folder / "deployment_inputs"
    out.mkdir(exist_ok=True)
    for name in ["images", "renders", "opacity"]:
        (out / name).mkdir(exist_ok=True)
    root = BASE.parents[1] / "scene_bench_v1"
    sources = [
        root / "render/ref_0003_0005_trainonly_v1/ref_0003_trainonly",
        root / "render/ref_0004_trainonly_v1/ref_0004_trainonly",
    ]
    frames = []
    provenance = []
    for i, stem in enumerate(sources):
        image = (
            Image.open(str(stem) + ".png")
            .convert("RGB")
            .resize((64, 64), Image.Resampling.LANCZOS)
        )
        image.save(out / "images" / f"{i:05d}.png")
        image.save(out / "renders" / f"{i:05d}.png")
        Image.fromarray(np.full((64, 64), 255, np.uint8)).save(
            out / "opacity" / f"{i:05d}.png"
        )
        cam = json.loads(Path(str(stem) + ".camera.json").read_text())
        k = np.array(cam["K"])
        k[0] *= 64 / cam["resolution"][0]
        k[1] *= 64 / cam["resolution"][1]
        matrix = np.array(cam["camera_to_world_cv"])
        matrix = matrix @ np.diag([1, -1, -1, 1])
        frames.append(
            dict(
                file_path=f"{i:05d}.png",
                transform_matrix=matrix.tolist(),
                w=64,
                h=64,
                fl_x=k[0, 0],
                fl_y=k[1, 1],
                cx=k[0, 2],
                cy=k[1, 2],
            )
        )
        provenance.append(
            dict(
                render=str(stem) + ".png",
                sha256=digest(str(stem) + ".png"),
                original_photo_used=False,
            )
        )
    write(out / "transforms.json", dict(camera_model="OPENCV", frames=frames))
    write(out / "selected_indices.json", [0])
    write(out / "target_indices.json", [1])
    with h5py.File(out / "caption.h5", "w") as f:
        ds = f.create_dataset(
            "synthetic_zero", data=np.zeros((4, 4096), dtype=np.uint16)
        )
        ds.attrs["caption"] = (
            "SYNTHETIC ZERO EMBEDDING FOR DEPLOYMENT SMOKE; NOT T5 ENCODING"
        )
    write(
        out / "split.json",
        dict(
            test={
                "starbucks_deployment_smoke": dict(
                    transforms_path="transforms.json",
                    image_root="images",
                    render_dir="renders",
                    opacity_dir="opacity",
                    selected_indices_path="selected_indices.json",
                    target_indices_path="target_indices.json",
                    prompt_path="caption.h5",
                    camera_scale=1.0,
                    has_gt=False,
                )
            }
        ),
    )
    write(
        out / "provenance.json",
        dict(
            status="deployment_smoke_only",
            sources=provenance,
            source_original_images_used=False,
            synthetic_caption_embedding=True,
            synthetic_all_opaque_masks=True,
            camera_calibration="Estimated and fixed local benchmark camera; not original calibrated phone poses",
            fidelity_scoring_forbidden=True,
            original_scene_mutated=False,
        ),
    )
    print(out / "split.json")


def artifixer_smoke(args):
    folder = BASE / "artifixer3d"
    report = json.loads((folder / "trial.json").read_text())
    start = time.monotonic()
    if report.get("actual_error"):
        report.setdefault("attempt_history", []).append(
            dict(
                error=report.pop("actual_error"),
                traceback=report.pop("traceback", None),
                recovered_after=report["status"],
            )
        )
    report.update(
        status="preparing_checkpoint_architecture",
        command=sys.argv,
        conditioning="explicitly synthetic zero text/ray tensors and deterministic random latent; no real original images",
        actual_output_kind="deployment_smoke_only",
        fidelity_scored=False,
        three_d_asset_modified=False,
    )
    write(folder / "trial.json", report)
    try:
        import torch

        torch.set_num_threads(2)
        sys.path.insert(0, str(folder / "repo"))
        from diffusers import WanTransformer3DModel
        from model_training.net.wan_transformer import ArtifixerTransformer

        ckpt = folder / "weights/artifixer-1.3b.pt"
        state = torch.load(ckpt, map_location="cpu", weights_only=True, mmap=True)
        config = json.loads((folder / "wan_transformer_config.json").read_text())
        from accelerate import init_empty_weights

        with init_empty_weights(include_buffers=False):
            base = WanTransformer3DModel.from_config(config)
            model = ArtifixerTransformer(
                base,
                frames_per_block=None,
                use_kv_cache_attention=False,
                vae_scale_factor_spatial=8,
                vae_scale_factor_temporal=4,
                gradient_checkpointing=False,
                checkpoint_every_n_blocks=1,
            )
        expected = model.state_dict()
        missing = sorted(expected.keys() - state.keys())
        unexpected = sorted(state.keys() - expected.keys())
        mismatched = [
            key
            for key in expected.keys() & state.keys()
            if expected[key].shape != state[key].shape
        ]
        report.update(
            checkpoint_bytes=ckpt.stat().st_size,
            checkpoint_tensor_count=len(state),
            missing_keys=missing,
            unexpected_keys=unexpected,
            shape_mismatches=mismatched,
            torch=torch.__version__,
            cpu_architecture_check_seconds=time.monotonic() - start,
        )
        if missing or unexpected or mismatched:
            raise ValueError(
                "Official checkpoint architecture differs from constructed wrapper; see ledger"
            )
        model.load_state_dict(state, assign=True)
        model.eval()
        report["status"] = "checkpoint_cpu_verified"
        write(folder / "trial.json", report)
        if not args.arti_gpu:
            return
        with open("/tmp/g2_starbucks_tour.gpu.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            device = "cuda"
            dtype = torch.bfloat16
            model = model.to(device=device, dtype=dtype)
            torch.manual_seed(20261108)
            torch.cuda.reset_peak_memory_stats()
            kwargs = dict(
                hidden_states=torch.randn(1, 16, 7, 8, 8, device=device, dtype=dtype),
                timestep=torch.tensor([500.0], device=device, dtype=dtype),
                encoder_hidden_states=torch.zeros(
                    1, 4, 4096, device=device, dtype=dtype
                ),
                neighbor_hidden_states=None,
                opacity=torch.ones(1, 25, 64, 64, device=device, dtype=dtype),
                camera_rays=torch.zeros(1, 7, 64, 64, 6, device=device, dtype=dtype),
                w2cs=torch.eye(4, device=device, dtype=dtype)[None, None].expand(
                    1, 7, 4, 4
                ),
                neighbor_w2cs=None,
                Ks=torch.eye(3, device=device, dtype=dtype)[None, None].expand(
                    1, 7, 3, 3
                ),
                neighbor_Ks=None,
                ignore_neighbors=True,
            )
            torch.cuda.synchronize()
            t = time.monotonic()
            with torch.inference_mode():
                result = model(**kwargs)
            torch.cuda.synchronize()
            dt = time.monotonic() - t
            if isinstance(result, tuple):
                result = result[0]
            if hasattr(result, "sample"):
                result = result.sample
            if not torch.is_tensor(result):
                raise TypeError(str(type(result)))
            report.update(
                status="completed_synthetic_transformer_smoke",
                gpu_inference_run=True,
                forward_seconds=dt,
                output_shape=list(result.shape),
                output_finite=bool(torch.isfinite(result).all()),
                output_mean=float(result.float().mean()),
                output_std=float(result.float().std()),
                peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                gpu_name=torch.cuda.get_device_name(0),
                elapsed_seconds=time.monotonic() - start,
                output_kind="synthetic_latent_deployment_smoke_not_image_or_3d",
            )
            torch.save(result.cpu(), folder / "synthetic_smoke_latent.pt")
            write(folder / "trial.json", report)
    except BaseException as exc:
        report.update(
            status="failed",
            actual_error=str(exc),
            traceback=traceback.format_exc(),
            elapsed_seconds=time.monotonic() - start,
        )
        write(folder / "trial.json", report)
        raise
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--arti-smoke", action="store_true")
    ap.add_argument("--arti-inputs", action="store_true")
    ap.add_argument("--arti-gpu", action="store_true")
    ap.add_argument(
        "--input",
        type=Path,
        default=Path(
            "/home/nvidia/algotithm/runs/starbucks_tour/scene_bench_v1/render/ref_0004_trainonly_v1/ref_0004_trainonly.png"
        ),
    )
    ap.add_argument("--long-edge", type=int, default=0)
    ap.add_argument("--output", default="outputs/ref4_noref.png")
    ap.add_argument("--trial-file", default="trial.json")
    args = ap.parse_args()
    if args.init:
        initial()
    elif args.arti_inputs:
        artifixer_inputs()
    elif args.arti_smoke:
        artifixer_smoke(args)
    else:
        run(args)
