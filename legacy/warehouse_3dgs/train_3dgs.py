"""Train 3DGS from Isaac-rendered views with exact poses (no COLMAP).
Poses: eye/target world (Z-up) -> OpenCV cam-to-world; intrinsics: same renderer.
Uses gsplat rasterization + simple dense Adam training (Mip-Splatting-free baseline)."""


def main():
    import os, json, glob, argparse, math
    import numpy as np, torch, gsplat
    from PIL import Image
    from torch import nn
    import torch.nn.functional as F

    p = argparse.ArgumentParser()
    p.add_argument("--views", default="views")
    p.add_argument("--out", default="3dgs_model")
    p.add_argument("--iters", type=int, default=8000)
    p.add_argument("--h", type=int, default=540)  # downscale train
    p.add_argument("--fov_deg", type=float, default=60.0)  # USD camera default FOV
    args = p.parse_args()

    dev = "cuda"
    torch.manual_seed(0)
    np.random.seed(0)
    files = sorted(glob.glob(os.path.join(args.views, "view_*.png")))
    poses = json.load(open(os.path.join(args.views, "poses.json")))
    if not files or len(files) != len(poses):
        raise ValueError("Nonempty images and matching poses required")
    print("views:", len(files))

    from simready_bridge.geometry import look_at_c2w as lookat_c2w

    imgs = []
    c2ws = []
    for f, pz in zip(files, poses):
        im = Image.open(f).convert("RGB")
        im = im.resize((round(args.h * 16 / 9), args.h), Image.LANCZOS)
        imgs.append(torch.tensor(np.array(im) / 255.0, dtype=torch.float32))
        c2ws.append(lookat_c2w(pz["eye"], pz["target"]))
    c2ws = torch.tensor(np.stack(c2ws))
    H, W = imgs[0].shape[:2]
    # intrinsics from USD camera fov (horizontal aperture default 20.955mm, focal 24.5? use fov arg)
    fx = fy = (W / 2) / math.tan(math.radians(args.fov_deg / 2))
    K = torch.tensor([[fx, 0, W / 2], [0, fy, H / 2], [0, 0, 1]], dtype=torch.float32)
    print("H,W", H, W, "fx", round(fx, 1))

    # initial points: random in scene bbox from poses spread
    pts = c2ws[:, :3, 3].cpu().numpy()
    mn = pts.min(0) - 1.0
    mx = pts.max(0) + 1.0
    N0 = 120_000
    means0 = torch.tensor(
        np.random.uniform(mn, mx, size=(N0, 3)), dtype=torch.float32
    ) * torch.tensor([1.0, 1.0, 0.15], dtype=torch.float32)  # flatter in z
    scales0 = torch.log(torch.tensor(np.full((N0, 3), 0.01), dtype=torch.float32))
    rgbs0 = torch.tensor(
        np.full((N0, 3), 0.5) + np.random.uniform(-0.05, 0.05, (N0, 3)),
        dtype=torch.float32,
    )

    class GS(nn.Module):
        def __init__(s):
            super().__init__()
            s.means = nn.Parameter(means0.to(dev))
            s.log_scale = nn.Parameter(scales0.to(dev))
            s.quats = nn.Parameter(
                torch.tensor([0.0, 0.0, 0.0, 1.0], device=dev).repeat(N0, 1)
                + torch.randn(N0, 4, device=dev) * 0.01
            )
            s.rgb = nn.Parameter(rgbs0.to(dev))
            s.logit_op = nn.Parameter(torch.zeros(N0, device=dev) - 2.0)

        def opacities(s):
            return torch.sigmoid(s.logit_op)

        def scales(s):
            return torch.exp(s.log_scale.clamp(-8, 1))

        def quats_n(s):
            q = F.normalize(s.quats, dim=1)
            return torch.cat([q[:, 3:], q[:, :3]], 1)  # xyzw->wxyz

    gs = GS().to(dev)
    lrs = [
        {"params": [gs.means], "lr": 1.6e-3 * 1.0},
        {"params": [gs.log_scale], "lr": 1.6e-3},
        {"params": [gs.quats], "lr": 1.6e-3},
        {"params": [gs.rgb], "lr": 2.5e-3},
        {"params": [gs.logit_op], "lr": 5e-3},
    ]
    opt = torch.optim.Adam(lrs)
    Kd = K.to(dev)
    Ks = Kd.unsqueeze(0)
    imgs_t = torch.stack(imgs).to(dev)
    c2ws_d = c2ws.to(dev)
    # w2c (opencv): R = c2w[:3,:3].T, t = -R @ c
    w2cs = []
    for i in range(len(files)):
        R = c2ws[i, :3, :3].T
        t = -R @ c2ws[i, :3, 3]
        T = torch.eye(4, dtype=torch.float32)
        T[:3, :3] = R
        T[:3, 3] = t
        w2cs.append(T)
    w2cs = torch.stack(w2cs).float().to(dev)
    bs = 1
    import time

    os.makedirs(args.out, exist_ok=True)
    for it in range(args.iters):
        opt.param_groups  # noop
        idx = torch.randint(0, len(files), (bs,))
        gt = imgs_t[idx]
        out, _, _ = gsplat.rasterization(
            means=gs.means.float(),
            quats=gs.quats_n().float(),
            scales=gs.scales().float(),
            opacities=gs.opacities().float(),
            colors=gs.rgb.clamp(0, 1).float(),
            viewmats=w2cs[idx].float(),
            Ks=Ks.float(),
            width=W,
            height=H,
            render_mode="RGB",
        )
        loss = F.mse_loss(out, gt)
        loss.backward()
        opt.step()
        opt.zero_grad()
        if it % 500 == 0 or it == args.iters - 1:
            with torch.no_grad():
                psnr = -10 * torch.log10(
                    F.mse_loss(
                        gsplat.rasterization(
                            means=gs.means.float(),
                            quats=gs.quats_n().float(),
                            scales=gs.scales().float(),
                            opacities=gs.opacities().float(),
                            colors=gs.rgb.clamp(0, 1).float(),
                            viewmats=w2cs[:8].float(),
                            Ks=K.float()
                            .to(dev)
                            .unsqueeze(0)
                            .expand(min(8, len(files)), -1, -1),
                            width=W,
                            height=H,
                            render_mode="RGB",
                        )[0],
                        imgs_t[:8],
                    )
                )
            print(
                it,
                "train_loss",
                round(loss.item(), 5),
                "psnr8",
                round(psnr.item(), 2),
                flush=True,
            )
    # save npz (standard 3DGS-ish)
    q = F.normalize(gs.quats, dim=1)
    np.savez(
        os.path.join(args.out, "model.npz"),
        means=gs.means.detach().cpu().numpy(),
        scales=gs.scales().detach().cpu().numpy(),
        quats=q.detach().cpu().numpy(),
        rgb=gs.rgb.detach().clamp(0, 1).cpu().numpy(),
        opacity=gs.opacities().detach().cpu().numpy(),
        K=K.numpy(),
        w2cs=w2cs.cpu().numpy(),
        H=H,
        W=W,
    )
    print("saved", args.out)


if __name__ == "__main__":
    main()
