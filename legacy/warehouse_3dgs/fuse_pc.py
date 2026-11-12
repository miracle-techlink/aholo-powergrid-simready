"""Fuse world-space pointclouds into gaussian splats (RGBD-fusion reconstruction).
Voxel-dedupe, opacity/scale from spacing; optional brief color polish."""


def main():
    import os, glob, json, argparse
    import numpy as np, torch

    p = argparse.ArgumentParser()
    p.add_argument("--views", default="views_pc")
    p.add_argument("--out", default="3dgs_fused")
    p.add_argument("--voxel", type=float, default=0.02)
    p.add_argument("--polish", type=int, default=1500)
    args = p.parse_args()
    if args.voxel <= 0 or args.polish < 0:
        raise ValueError("Positive voxel and nonnegative polish required")
    files = sorted(glob.glob(os.path.join(args.views, "pc_*.npz")))
    print("views", len(files))
    P = []
    C = []
    for f in files:
        d = np.load(f)
        P.append(d["pts"])
        C.append(d["rgb"])
    P = np.concatenate(P)
    C = np.concatenate(C).astype(np.float32) / 255.0
    print("raw points", len(P))
    # voxel dedupe (hash on quantized coords), average color per voxel
    q = np.floor(P / args.voxel).astype(np.int64)
    uk, inv = np.unique(q, axis=0, return_inverse=True)
    cnt = np.bincount(inv)
    sumc = np.zeros((len(uk), 3), np.float64)
    np.add.at(sumc, inv, C)
    colors = (sumc / cnt[:, None]).astype(np.float32)
    # voxel center-of-mass
    sump = np.zeros((len(uk), 3), np.float64)
    np.add.at(sump, inv, P)
    means = (sump / cnt[:, None]).astype(np.float32)
    print("deduped", len(means))
    s = args.voxel * 1.1
    scales = np.full((len(means), 3), s, dtype=np.float32)
    op = np.clip(cnt / 3.0, 0.55, 0.92).astype(
        np.float32
    )  # seen more -> denser->more opaque
    quats = np.tile(np.array([0.0, 0.0, 0.0, 1.0], np.float32), (len(means), 1))
    W, H = 960, 540
    fx = fy = 2290.6
    cx, cy = 480.0, 270.0
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], np.float32)
    poses = json.load(open(os.path.join(args.views, "poses.json")))
    os.makedirs(args.out, exist_ok=True)
    from simready_bridge.geometry import look_at_c2w, rigid_inverse

    def c2w_of(pz):
        return look_at_c2w(pz["eye"], pz["target"])

    w2cs = []
    for pz in poses:
        M = c2w_of(pz)
        R = M[:3, :3].T
        t = -R @ M[:3, 3]
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = t
        w2cs.append(T)
    w2cs = np.stack(w2cs).astype(np.float32)
    np.savez(
        os.path.join(args.out, "model.npz"),
        means=means,
        scales=scales,
        quats=quats,
        rgb=colors,
        opacity=op,
        K=K,
        w2cs=w2cs,
        H=H,
        W=W,
    )
    if args.polish == 0:
        return
    import torch.nn.functional as F, gsplat

    dev = "cuda"
    w2cs = torch.tensor(np.stack(w2cs), dtype=torch.float32, device=dev)
    w2cs = w2cs.float()
    Kd = torch.tensor(K, device=dev)
    from PIL import Image

    imgs = []
    for f in sorted(glob.glob(os.path.join(args.views, "view_*.png"))):
        imgs.append(
            torch.tensor(
                (np.asarray(Image.open(f).convert("RGB")) / 255.0),
                dtype=torch.float32,
                device=dev,
            )
        )
    imgs = torch.stack(imgs)
    means_t = torch.tensor(means, dtype=torch.float32, device=dev)
    scales_t = torch.tensor(scales, dtype=torch.float32, device=dev)
    q_t = torch.tensor(quats, dtype=torch.float32, device=dev)
    q_t = torch.cat([q_t[:, 3:], q_t[:, :3]], 1)
    op_t = torch.logit(
        torch.tensor(op, dtype=torch.float32, device=dev)
    ).requires_grad_(True)
    sc_t = scales_t.log().requires_grad_(True)
    rgb_t = torch.tensor(colors, dtype=torch.float32, device=dev).requires_grad_(True)
    opt = torch.optim.Adam(
        [
            {"params": [rgb_t], "lr": 2e-3},
            {"params": [op_t], "lr": 3e-3},
            {"params": [sc_t], "lr": 8e-3},
        ]
    )
    n = len(imgs)
    for it in range(args.polish):
        i = torch.randint(0, n, (1,)).item()
        out, _, _ = gsplat.rasterization(
            means=means_t,
            quats=q_t,
            scales=sc_t.exp().clamp(1e-4, 0.5),
            opacities=op_t.sigmoid(),
            colors=rgb_t.clamp(0, 1),
            viewmats=w2cs[i : i + 1],
            Ks=Kd.unsqueeze(0),
            width=W,
            height=H,
            render_mode="RGB",
        )
        loss = F.mse_loss(out, imgs[i : i + 1])
        loss.backward()
        opt.step()
        opt.zero_grad()
        if it % 300 == 0:
            print(it, round(loss.item(), 5), flush=True)
    # eval psnr on 8 views
    with torch.no_grad():
        ps = []
        for i in range(0, n, max(1, n // 8)):
            out, _, _ = gsplat.rasterization(
                means=means_t,
                quats=q_t,
                scales=sc_t.exp().clamp(1e-4, 0.5),
                opacities=op_t.sigmoid(),
                colors=rgb_t.clamp(0, 1),
                viewmats=w2cs[i : i + 1],
                Ks=Kd.unsqueeze(0),
                width=W,
                height=H,
                render_mode="RGB",
            )
            ps.append(-10 * torch.log10(F.mse_loss(out, imgs[i : i + 1])))
        print("PSNR", round(torch.stack(ps).mean().item(), 2))
    np.savez(
        os.path.join(args.out, "model.npz"),
        means=means_t.cpu().numpy(),
        scales=sc_t.exp().clamp(1e-4, 0.5).detach().cpu().numpy(),
        quats=q_t[:, [1, 2, 3, 0]].cpu().numpy(),
        rgb=rgb_t.detach().clamp(0, 1).cpu().numpy(),
        opacity=op_t.sigmoid().detach().cpu().numpy(),
        K=K,
        w2cs=w2cs.cpu().numpy(),
        H=H,
        W=W,
    )
    print("saved", args.out)


if __name__ == "__main__":
    main()
