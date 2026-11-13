"""Create captions and an exportable contact-evidence plot from measured data."""

import argparse
import json
from pathlib import Path

import numpy as np

PHASES = {
    "SETTLE": "定位完成 · 准备抓取",
    "APPROACH": "机械臂靠近杯子",
    "CLOSE": "闭爪 · 建立双侧接触",
    "LIFT": "抬起杯子",
    "HOLD": "离桌保持",
    "TRANSFER": "搬到第二张桌子",
    "PLACE": "下降 · 放到桌面",
    "RELEASE": "松开夹爪",
    "RETREAT": "机械臂退回",
    "VERIFY": "确认放稳 · 任务完成",
}


def timestamp(seconds):
    centiseconds = round(seconds * 100)
    return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02d}:{centiseconds // 100 % 60:02d}.{centiseconds % 100:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--scene-contract", type=Path)
    args = ap.parse_args()
    root = args.run
    original = bool(
        args.scene_contract
        and json.loads(args.scene_contract.read_text()).get("appearance")
        == "original_video_reconstruction"
    )
    title = (
        "星巴克原视频重建场景 | G2 将杯子搬到另一张桌子"
        if original
        else "星巴克任务区 | G2 将重建杯子搬到另一张桌子"
    )
    note = (
        "PhysX 仿真 · 固定基座 · 原重建背景保留 · 材料参数未实测"
        if original
        else "PhysX 仿真 · 固定基座 · 实测轨迹回放 · 场景局部补建 / 材料参数未实测"
    )
    audit = json.loads((root / "artifacts/audit.json").read_text())
    if audit["status"] != "PASS":
        raise ValueError("A passed independent audit is required")
    data = np.load(root / "artifacts/physics/rollout.npz", allow_pickle=False)
    dt = float(data["dt"])
    t = np.arange(len(data["poses"])) * dt
    cup = data["poses"][:, -1]
    force = np.linalg.norm(data["forces"], axis=-1)
    duration = len(t) * dt
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Noto Sans CJK SC,38,&H00FFFFFF,&H00FFFFFF,&HAA171717,&HAA171717,0,0,0,0,100,100,0,0,3,12,0,7,42,42,28,1
Style: Phase,Noto Sans CJK SC,34,&H00FFFFFF,&H00FFFFFF,&HAA171717,&HAA171717,0,0,0,0,100,100,0,0,3,10,0,1,42,42,90,1
Style: Note,Noto Sans CJK SC,23,&H00DDDDDD,&H00FFFFFF,&HAA171717,&HAA171717,0,0,0,0,100,100,0,0,3,7,0,1,42,42,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,0:00:00.00,{timestamp(duration)},Title,,0,0,0,,{title}",
        f"Dialogue: 0,0:00:00.00,{timestamp(duration)},Note,,0,0,0,,{note}",
    ]
    for i in range(0, len(t), 24):
        end = min(i + 24, len(t)) * dt
        label = PHASES[str(data["phases"][i])]
        telemetry = f"夹爪接触 {force[i, 0]:.1f} / {force[i, 1]:.1f} N   目标桌面承托 {force[i, 3]:.2f} N"
        events.append(
            f"Dialogue: 0,{timestamp(t[i])},{timestamp(end)},Phase,,0,0,0,,{label}\\N{telemetry}"
        )
    (root / "captions.ass").write_text(header + "\n".join(events) + "\n")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    from scipy.spatial.transform import Rotation

    plt.rcParams.update(
        {
            "font.family": "Noto Sans CJK JP",
            "font.size": 10,
            "axes.unicode_minus": False,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), layout="constrained")
    axes[0, 0].plot(t, cup[:, 2] - 0.85, color="#2167a8")
    axes[0, 0].axhline(0.10, color="#999999", linestyle="--", linewidth=1)
    axes[0, 0].set(
        xlabel="时间 / s", ylabel="杯底原点离桌高度 / m", title="A. 抬起、搬运、放下"
    )
    for j, name in enumerate(["左夹指", "右夹指", "源桌面", "目标桌面"]):
        axes[0, 1].plot(t, force[:, j], label=name, linewidth=0.9)
    axes[0, 1].set(
        xlabel="时间 / s", ylabel="接触力范数 / N", title="B. 实测接触力（含瞬态峰值）"
    )
    axes[0, 1].legend(ncol=2, frameon=False)
    for xy, name in [((0.65, -0.13), "桌 A"), ((0.4, -0.94), "桌 B")]:
        axes[1, 0].add_patch(
            Circle(xy, 0.32, facecolor="#f6ecde", edgecolor="#8a7354", alpha=0.6)
        )
        axes[1, 0].text(*xy, name, ha="center", va="center")
    axes[1, 0].plot(cup[:, 0], cup[:, 1], color="#2167a8")
    axes[1, 0].scatter(*cup[0, :2], c="#2167a8", label="起点")
    axes[1, 0].scatter(*cup[-1, :2], c="#b23e35", label="终点")
    axes[1, 0].set(
        xlabel="世界 X / m",
        ylabel="世界 Y / m",
        title="C. 实际跨桌轨迹",
        aspect="equal",
    )
    axes[1, 0].legend(frameon=False)
    tilt = np.degrees(
        np.arccos(np.clip(Rotation.from_quat(cup[:, 3:]).as_matrix()[:, 2, 2], -1, 1))
    )
    axes[1, 1].plot(t, tilt, color="#279177")
    axes[1, 1].set(
        xlabel="时间 / s", ylabel="杯体倾角 / °", title="D. 搬运与释放后的稳定性"
    )
    for ax in axes.ravel():
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.15)
    fig.suptitle("G2 双桌搬杯 | PhysX 实测证据 · 材料参数未实测", fontsize=15)
    fig.savefig(root / "contact_evidence.png", dpi=180)
    fig.savefig(root / "contact_evidence.pdf")
    plt.close(fig)


if __name__ == "__main__":
    main()
