"""Single-factor patches for the external, frozen Isaac deployment adapter."""


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("Unsupported deployment source anchor: " + old[:100])
    return source.replace(old, new, 1)


def render_variant(
    source: str,
    *,
    viewport_after: int | None = None,
    disable_saved_rgb: bool = False,
    disable_multitick: bool = False,
    single_clock_startup: bool = False,
) -> str:
    """Preserve startup history before disabling only the preview viewport."""
    if single_clock_startup:
        if disable_multitick:
            raise ValueError("Choose a startup or runtime clock change, not both")
        source = _replace_once(
            source,
            "experience='isaacsim.exp.base.kit').app;apps.append(app)",
            "experience='isaacsim.exp.base.kit', kit_args='--/rtx/hydra/supportMultiTickRate=false').app;apps.append(app)",
        )
    if viewport_after is not None:
        if type(viewport_after) is not int or viewport_after < 1:
            raise ValueError("Viewport switch must follow a complete startup frame")
        anchor = "        for ordinal,i in enumerate(selected):\n"
        source = _replace_once(
            source,
            anchor,
            anchor
            + f"            if ordinal == {viewport_after}:\n"
            + "                from omni.kit.viewport.utility import get_active_viewport\n"
            + "                preview = get_active_viewport()\n"
            + "                if preview is not None: preview.updates_enabled = False\n"
            + (
                "                settings.set('/rtx/hydra/supportMultiTickRate', False)\n"
                if disable_multitick
                else ""
            )
            + (
                "                from pxr import UsdRender\n"
                "                for saved_product in list(stage.Traverse()):\n"
                "                    if saved_product.IsA(UsdRender.Product):\n"
                "                        cameras = UsdRender.Product(saved_product).GetCameraRel().GetTargets()\n"
                "                        if list(map(str, cameras)) == ['/World/TaskRGBCamera']:\n"
                "                            saved_product.SetActive(False)\n"
                if disable_saved_rgb
                else ""
            ),
        )
    elif disable_saved_rgb or disable_multitick:
        raise ValueError("Render changes require an explicit post-startup boundary")
    compile(source, "render_variant.py", "exec")
    return source


def physics_variant(
    source: str, *, device: str | None = None, cache_host_reads: bool = False
) -> str:
    """Benchmark a backend without changing dt, solver iterations or checks."""
    if device is not None:
        if device not in ("cpu", "cuda:0"):
            raise ValueError("Unsupported benchmark device")
        source = _replace_once(
            source,
            "SimulationCfg(dt=dt, device='cuda:0',",
            f"SimulationCfg(dt=dt, device='{device}',",
        )
    if cache_host_reads:
        source = _replace_once(
            source,
            "    def cpu(x):\n"
            "        if hasattr(x, 'torch'): x = x.torch\n"
            "        return x.detach().cpu().numpy() if hasattr(x, 'detach') else x.numpy()\n",
            "    host_reads = {}\n"
            "    def cpu(x):\n"
            "        if hasattr(x, 'torch'): x = x.torch\n"
            "        if not hasattr(x, 'detach'): return x.numpy()\n"
            "        key = (x.data_ptr(), tuple(x.shape), tuple(x.stride()), x.dtype, x._version)\n"
            "        if key not in host_reads:\n"
            "            host_reads[key] = (x, x.detach().cpu().numpy())\n"
            "        return host_reads[key][1]\n",
        )
        # Warp/PhysX writes bypass PyTorch's version counter. Invalidate around
        # every physics epoch; retain source tensors to prevent pointer recycling.
        source = _replace_once(
            source,
            "                t=step*dt; root=cpu(robot.data.root_state_w)[0]\n",
            "                host_reads.clear()\n"
            "                t=step*dt; root=cpu(robot.data.root_state_w)[0]\n",
        )
        source = _replace_once(
            source,
            "                sim.step(render=False);robot.update(dt);caps.update(dt);bases.update(dt)\n",
            "                sim.step(render=False);robot.update(dt);caps.update(dt);bases.update(dt)\n"
            "                host_reads.clear()\n",
        )
        source = _replace_once(
            source,
            "    def acquire(label, yaw_degrees, pitch_degrees):\n",
            "    def acquire(label, yaw_degrees, pitch_degrees):\n"
            "        host_reads.clear()\n",
        )
        source = _replace_once(
            source,
            "        return record\n    def scan(label, views):\n",
            "        host_reads.clear()\n"
            "        return record\n    def scan(label, views):\n",
        )
    compile(source, "physics_variant.py", "exec")
    return source


def render_prefix_canary(source: str, frames: int) -> str:
    """Use a continuous prefix with normal subframes, never the --frames path."""
    if type(frames) is not int or frames < 2:
        raise ValueError("Canary requires at least two frames")
    anchor = "    selected=list(map(int,a.frames.split(','))) if a.frames else list(range(len(data['steps'])))"
    source = _replace_once(
        source, anchor, anchor + f"\n    selected=selected[:{frames}]"
    )
    compile(source, "render_prefix_canary.py", "exec")
    return source


def sam_cuda_variant(source: str) -> str:
    """Use the same SAM weights, FP32 and generator parameters on CUDA."""
    source = _replace_once(
        source,
        "    masks = seg.sam_segment(rgb, points_per_side=16)\n",
        "    from segment_anything import SamAutomaticMaskGenerator, sam_model_registry\n"
        "    checkpoint = Path.home() / '.cache/sam/sam_vit_b_01ec64.pth'\n"
        "    if not checkpoint.is_file(): raise FileNotFoundError(checkpoint)\n"
        "    torch.backends.cuda.matmul.allow_tf32 = False\n"
        "    torch.backends.cudnn.allow_tf32 = False\n"
        "    sam = sam_model_registry['vit_b'](checkpoint=str(checkpoint)).to(device='cuda')\n"
        "    predictor = SamAutomaticMaskGenerator(sam, points_per_side=16, pred_iou_thresh=.86, stability_score_thresh=.92)\n"
        "    masks = seg.sam_segment(rgb, predictor=predictor, points_per_side=16)\n",
    )
    source = _replace_once(source, "native RGB (CPU).", "native RGB (CUDA FP32).")
    compile(source, "sam_cuda_variant.py", "exec")
    return source
