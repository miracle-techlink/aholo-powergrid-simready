"""绘制适合竖版技术白皮书的新视频执行路径；输出可编辑矢量图。"""

import argparse
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/report/figures"
INK = "#273646"
MUTED = "#5f7288"
BLUE = "#376f99"
AMBER = "#ae792b"
TEAL = "#2c837c"


def build(output=OUTPUT, *, export_png=False):
    mpl.rcParams.update(
        {
            "font.family": ["Noto Sans CJK SC", "DejaVu Sans"],
            "svg.fonttype": "none",
            "svg.hashsalt": "simready-new-video-zh-portrait-v1",
            # Noto CJK uses CFF outlines; Type 3 embeds them without a TrueType mismatch.
            "pdf.fonttype": 3,
            "axes.unicode_minus": False,
        }
    )
    fig, ax = plt.subplots(figsize=(7.2, 9.5))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 7.2), ylim=(0, 9.5))
    ax.axis("off")

    def text(x, y, value, size=10.5, color=INK, weight="normal", **kwargs):
        return ax.text(
            x,
            y,
            value,
            fontsize=size,
            color=color,
            weight=weight,
            ha="left",
            va="center",
            linespacing=1.5,
            **kwargs,
        )

    def box(x, y, w, h, color, fill="white", dashed=False):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.02,rounding_size=0.05",
                facecolor=fill,
                edgecolor=color,
                linewidth=0.9,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )

    def arrow(points, color=INK, dashed=False):
        for a, b in zip(points[:-2], points[1:-1], strict=False):
            ax.plot(
                [a[0], b[0]],
                [a[1], b[1]],
                color=color,
                lw=1.1,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        ax.add_patch(
            FancyArrowPatch(
                points[-2],
                points[-1],
                arrowstyle="-|>",
                mutation_scale=10,
                color=color,
                linewidth=1.1,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )

    text(0.25, 9.18, "新视频入口：从输入证据到场景候选", 16.5, weight="bold")
    text(
        0.25,
        8.86,
        "当前可执行路径：观测登记 → 重建接入 → 场景构建 → 结构检查与巡览",
        9.8,
        MUTED,
    )

    box(0.23, 6.05, 6.74, 2.55, "#becbd6")
    text(0.43, 8.34, "（一）来源与重建：保留原片，绑定处理产物", 12, BLUE, "bold")
    box(0.45, 7.37, 2.55, 0.66, BLUE, "#f0f5fa")
    text(0.61, 7.82, "原始场景视频", 11.3, BLUE, "bold")
    text(0.61, 7.57, "原始字节与文件哈希", 10)
    box(3.80, 7.37, 2.95, 0.66, BLUE, "#f0f5fa")
    text(3.96, 7.82, "参考观测登记", 11.3, BLUE, "bold")
    text(3.96, 7.57, "抽帧、名义采样时间、帧哈希", 9.7)
    arrow([(3.03, 7.70), (3.77, 7.70)], BLUE)
    arrow([(1.725, 7.34), (1.725, 7.14)], BLUE)
    box(0.45, 6.28, 2.55, 0.82, BLUE)
    text(0.61, 6.90, "云重建 / 离线产物导入", 10.8, BLUE, "bold")
    text(0.61, 6.64, "完整原片供云服务；保存凭据", 9.3)
    text(0.61, 6.42, "或复用带来源声明的 PLY", 9.3, MUTED)
    box(3.80, 6.28, 2.95, 0.82, BLUE, "#f0f5fa")
    text(3.96, 6.90, "只读执行输入", 11.3, BLUE, "bold")
    text(3.96, 6.64, "PLY + 场景规格 + 来源记录", 9.8)
    text(3.96, 6.42, "按哈希绑定输入与配置", 9.8, MUTED)
    arrow([(3.03, 6.70), (3.77, 6.70)], BLUE)
    arrow([(5.275, 7.34), (5.275, 7.14)], BLUE)
    arrow([(3.60, 6.02), (3.60, 5.78)], AMBER)

    box(0.23, 3.55, 6.74, 2.20, "#becbd6")
    text(0.43, 5.50, "（二）场景构建：外观、碰撞与声明对象分层", 12, AMBER, "bold")
    for x, title, detail in [
        (0.45, "可见高斯外观", "解码球谐与坐标变换"),
        (2.65, "有界碰撞代理", "按占据体素估计几何"),
        (4.85, "USD 组合与记录", "保存来源、对象和限位"),
    ]:
        box(x, 4.50, 1.90, 0.65, AMBER, "#fbf7ef")
        text(x + 0.12, 4.94, title, 10.3, AMBER, "bold")
        text(x + 0.12, 4.68, detail, 9.1)
    arrow([(2.38, 4.82), (2.62, 4.82)], AMBER)
    arrow([(4.58, 4.82), (4.82, 4.82)], AMBER)
    box(0.45, 3.77, 6.30, 0.49, AMBER, "white", dashed=True)
    text(
        0.60,
        4.01,
        "可选显式规格：坐标与尺度来源、地面、盒子 / 弹簧按钮、相机与渲染",
        9.4,
        AMBER,
    )
    arrow([(3.60, 4.29), (3.60, 4.47)], AMBER, dashed=True)
    arrow([(3.60, 3.52), (3.60, 3.28)], TEAL)

    box(0.23, 1.17, 6.74, 2.08, "#becbd6")
    text(0.43, 3.00, "（三）独立检查与交付：记录实际通过的范围", 12, TEAL, "bold")
    for x, title, detail in [
        (0.45, "结构检查", "重读 USD 层、边界与关节"),
        (3.80, "原生相机巡览", "相机轨迹、图像帧与编码"),
    ]:
        box(x, 2.03, 2.95 if x > 1 else 2.55, 0.66, TEAL, "#eef7f5")
        text(x + 0.14, 2.48, title, 11.0, TEAL, "bold")
        text(x + 0.14, 2.21, detail, 9.2)
    arrow([(3.03, 2.36), (3.77, 2.36)], TEAL)
    text(0.48, 1.72, "仅导出：场景文件与结构证据", 10, TEAL, "bold")
    text(3.82, 1.72, "含巡览：视频与相机、编码证据", 10, TEAL, "bold")
    text(
        0.48,
        1.43,
        "交付凭据绑定产物哈希；此路径尚未完成机器人任务与独立尺度验收。",
        9.5,
        MUTED,
    )

    box(0.23, 0.22, 6.74, 0.71, "#c8d2dc", "#f5f7fa")
    text(0.43, 0.69, "失败与恢复", 10.3, MUTED, "bold")
    text(1.55, 0.69, "失败即停并保留证据；身份与产物哈希一致才复用阶段。", 9.3)
    text(
        0.43,
        0.42,
        "无尺度锚点：保留未验证；向上方向未知：不猜地面；质量与任务另行验收。",
        9.2,
        MUTED,
    )

    output.mkdir(parents=True, exist_ok=True)
    stem = output / "new_video_pipeline"
    fig.savefig(stem.with_suffix(".svg"), metadata={"Date": None})
    fig.savefig(
        stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None}
    )
    if export_png:
        png_dir = ROOT / "dist/report-figures"
        png_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(png_dir / "new_video_pipeline.png", dpi=240)
    stem.with_suffix(".svg").write_text(
        "\n".join(
            line.rstrip() for line in stem.with_suffix(".svg").read_text().splitlines()
        )
        + "\n"
    )
    plt.close(fig)
    print(stem)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument(
        "--export-png",
        action="store_true",
        help="Also export PNGs to dist/report-figures",
    )
    args = parser.parse_args()
    build(args.out, export_png=args.export_png)
