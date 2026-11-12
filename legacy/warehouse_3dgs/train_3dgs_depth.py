"""Depth-initialized 3DGS: backproject Isaac depth -> exact surface points as gaussian init."""


def main():
    import os, json, glob, argparse, math
    import numpy as np, torch, gsplat
    from PIL import Image
    from torch import nn
    import torch.nn.functional as F

    p = argparse.ArgumentParser()
    p.add_argument("--views", default="views_c")
    p.add_argument("--out", default="3dgs_v2")
    p.add_argument("--iters", type=int, default=6000)
    p.add_argument("--h", type=int, default=540)
    p.add_argument(
        "--fx-at-960",
        type=float,
        required=True,
        help="Measured horizontal focal length at width 960; do not infer from a default",
    )
    p.add_argument("--pts_per_view", type=int, default=3000)
    args = p.parse_args()
    dev = "cuda"
    torch.manual_seed(0)
    np.random.seed(0)

    files = sorted(glob.glob(os.path.join(args.views, "view_*.png")))
    deps = sorted(glob.glob(os.path.join(args.views, "depth_*.npy")))
    poses = json.load(open(os.path.join(args.views, "poses.json")))
    if not files or not len(files) == len(deps) == len(poses):
        raise ValueError("Nonempty matched RGB, depth and poses required")
    print("views", len(files))

    from simready_bridge.geometry import look_at_c2w as lookat_c2w

    H = args.h
    W = round(H * 16 / 9)
    # exact intrinsics from isaacsim Camera API (focalLength 50mm @ 960x540 -> fx 2290.6)
    fx = fy = args.fx_at_960 * (W / 960.0)
    cx = W / 2
    cy = H / 2
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float32)

    imgs = []
    w2cs = []
    init_pts = []
    init_rgb = []
    rng = np.random.default_rng(0)
    for f, df, pz in zip(files, deps, poses):
        im = Image.open(f).convert("RGB").resize((W, H), Image.LANCZOS)
        img = np.asarray(im, dtype=np.float32) / 255.0
        d = np.load(df).astype(np.float32)
        if d.shape != (H, W):  # depth annotator at native res; resample
            dimg = Image.fromarray(d.astype(np.float32), mode="F").resize(
                (W, H), Image.NEAREST
            )
            d = np.asarray(dimg)
        imgs.append(torch.tensor(img))
        c2w = lookat_c2w(pz["eye"], pz["target"])
        R = c2w[:3, :3].T
        t = -R @ c2w[:3, 3]
        T = np.eye(4, dtype=np.float32)
        T[:3, :3] = R
        T[:3, 3] = t
        w2cs.append(T)
        # backproject subsample
        valid = d[np.isfinite(d) & (d > 0.1) & (d < 60)]
        if len(valid) < 10:
            continue
        n = min(args.pts_per_view, len(valid))
        ys, xs = np.where((np.isfinite(d) & (d > 0.1) & (d < 60)))
        sel = rng.choice(len(xs), n, replace=False)
        xs_s = xs[sel]
        ys_s = ys[sel]
        ds = d[ys_s, xs_s]
        xc = (xs_s - cx) / fx
        yc = (ys_s - cy) / fy
        pcam = np.stack([xc * ds, yc * ds, ds], 0)  # opencv cam frame
        pworld = (c2w[:3, :3] @ pcam).T + c2w[:3, 3]
        init_pts.append(pworld)
        init_rgb.append(img[ys_s, xs_s])
    if not init_pts:
        raise ValueError("No valid depth observations")
    means0 = torch.tensor(np.concatenate(init_pts), dtype=torch.float32)
    rgb0 = torch.tensor(np.concatenate(init_rgb), dtype=torch.float32)
    w2cs = torch.tensor(np.stack(w2cs))
    print("init gaussians:", len(means0))

    # scale init from local spacing
    N = len(means0)

    class GS(nn.Module):
        def __init__(s):
            super().__init__()
            s.means = nn.Parameter(means0.to(dev))
            s.log_scale = nn.Parameter(torch.log(torch.full((N, 3), 0.02, device=dev)))
            s.quats = nn.Parameter(
                torch.tensor([0.0, 0.0, 0.0, 1.0], device=dev).repeat(N, 1)
                + torch.randn(N, 4, device=dev) * 0.005
            )
            s.rgb = nn.Parameter(rgb0.to(dev))
            s.logit_op = nn.Parameter(torch.zeros(N, device=dev) - 1.0)

        def opacities(s):
            return torch.sigmoid(s.logit_op)

        def scales(s):
            return torch.exp(s.log_scale.clamp(-9, 0.5))

        def quats_n(s):
            q = F.normalize(s.quats, dim=1)
            return torch.cat([q[:, 3:], q[:, :3]], 1)

    gs = GS().to(dev)
    opt = torch.optim.Adam(
        [
            {"params": [gs.means], "lr": 3e-4},
            {"params": [gs.log_scale], "lr": 8e-4},
            {"params": [gs.quats], "lr": 3e-4},
            {"params": [gs.rgb], "lr": 2.5e-3},
            {"params": [gs.logit_op], "lr": 3e-3},
        ]
    )
    Kd = torch.tensor(K).to(dev)
    imgs_t = torch.stack(imgs).to(dev)
    w2cs = w2cs.float().to(dev)
    os.makedirs(args.out, exist_ok=True)
    for it in range(args.iters):
        idx = torch.randint(0, len(files), (1,))
        out, _, _ = gsplat.rasterization(
            means=gs.means.float(),
            quats=gs.quats_n().float(),
            scales=gs.scales().float(),
            opacities=gs.opacities().float(),
            colors=gs.rgb.clamp(0, 1).float(),
            viewmats=w2cs[idx].float(),
            Ks=Kd.unsqueeze(0),
            width=W,
            height=H,
            render_mode="RGB",
        )
        loss = F.mse_loss(out, imgs_t[idx])
        loss.backward()
        opt.step()
        opt.zero_grad()
        if it % 500 == 0 or it == args.iters - 1:
            with torch.no_grad():
                n8 = min(16, len(files))
                o = gsplat.rasterization(
                    means=gs.means.float(),
                    quats=gs.quats_n().float(),
                    scales=gs.scales().float(),
                    opacities=gs.opacities().float(),
                    colors=gs.rgb.clamp(0, 1).float(),
                    viewmats=w2cs[:n8].float(),
                    Ks=Kd.unsqueeze(0).expand(n8, -1, -1),
                    width=W,
                    height=H,
                    render_mode="RGB",
                )[0]
                psnr = -10 * torch.log10(F.mse_loss(o, imgs_t[:n8]))
            print(
                it,
                "loss",
                round(loss.item(), 5),
                "psnr",
                round(psnr.item(), 2),
                flush=True,
            )
    q = F.normalize(gs.quats, dim=1)
    np.savez(
        os.path.join(args.out, "model.npz"),
        means=gs.means.detach().cpu().numpy(),
        scales=gs.scales().detach().cpu().numpy(),
        quats=q.detach().cpu().numpy(),
        rgb=gs.rgb.detach().clamp(0, 1).cpu().numpy(),
        opacity=gs.opacities().detach().cpu().numpy(),
        K=K,
        w2cs=w2cs.cpu().numpy(),
        H=H,
        W=W,
    )
    print("saved", args.out)


if __name__ == "__main__":
    main()
