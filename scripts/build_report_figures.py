"""Rebuild the historical report's measured plots from published CSV and evidence."""

import argparse
import csv
import json
from pathlib import Path


def build_plots(root, output=None, *, export_png=False):
    """Render the two published data plots without changing their source data."""
    figures = output if output is not None else root / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    png_dir = Path(__file__).resolve().parents[1] / "dist/report-figures"
    if export_png:
        png_dir.mkdir(parents=True, exist_ok=True)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Noto Sans CJK SC",
                "Noto Sans CJK JP",
                "Droid Sans Fallback",
                "DejaVu Sans",
            ],
            "font.size": 11,
            "axes.unicode_minus": False,
            # Type 3 outlines also embed CJK OpenType/CFF fonts without
            # mislabelling the embedded program as a TrueType font.
            "pdf.fonttype": 3,
            # Embed glyph outlines so standalone SVGs retain the Chinese labels.
            "svg.fonttype": "path",
        }
    )
    with (root / "contact_trace.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    figure, axes = plt.subplots(
        3, 1, figsize=(7.4, 5.4), sharex=True, constrained_layout=True
    )
    figure.suptitle("历史任务接触测量", fontsize=14)
    keys = ("target_contact_N", "target_depth_m", "tool_compression_m")
    labels = ("目标接触力（N）", "按钮行程（mm）", "工具压缩（mm）")
    for axis, key, label, color in zip(
        axes, keys, labels, ("#2176ae", "#2b9365", "#b56f23"), strict=True
    ):
        axis.plot(
            [float(row["t"]) for row in rows],
            [float(row[key]) * (1 if key.endswith("_N") else 1000) for row in rows],
            color=color,
            lw=1.6,
        )
        axis.set_ylabel(label)
        axis.grid(alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
    axes[-1].set_xlabel("仿真时间（秒）")
    generated = []
    for extension in ("pdf", "svg") + (("png",) if export_png else ()):
        target = (
            png_dir if extension == "png" else figures
        ) / f"contact_trace.{extension}"
        figure.savefig(target)
        generated.append(target)
    plt.close(figure)
    values = json.loads((root / "evidence.json").read_text())[
        "historical_engineering_wall_seconds"
    ]
    labels = {
        "Recording x3": "录像（3 次）",
        "Physics x3": "物理仿真（3 次）",
        "Engineering intervals": "工程操作间隔",
        "Assets / frame checks": "资产与帧检查",
        "Non-overlap audit": "非重叠审计时段",
    }
    figure, axis = plt.subplots(figsize=(7.4, 3.8), constrained_layout=True)
    axis.barh(
        [labels[key] for key in values],
        list(values.values()),
        color=["#2176ae", "#2b9365", "#b56f23", "#7699ae", "#86988e"],
    )
    axis.invert_yaxis()
    axis.set_title("历史工程窗口耗时（非单次端到端运行）", fontsize=13, pad=12)
    axis.set_xlabel(f"实际耗时（秒）；合计 {sum(values.values()):.2f} 秒")
    axis.set_xlim(0, max(values.values()) * 1.3)
    axis.set_axisbelow(True)
    axis.grid(axis="x", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    for index, value in enumerate(values.values()):
        axis.text(value + 15, index, f"{value:.2f} 秒", va="center", fontsize=10)
    for extension in ("pdf", "svg") + (("png",) if export_png else ()):
        target = (png_dir if extension == "png" else figures) / f"timing.{extension}"
        figure.savefig(target)
        generated.append(target)
    plt.close(figure)
    return generated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plots-only",
        action="store_true",
        help="兼容旧命令；当前仅生成接触曲线与耗时图",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--export-png",
        action="store_true",
        help="Also export PNGs to dist/report-figures",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1] / "docs/report"
    generated = build_plots(root, args.out, export_png=args.export_png)
    for source in generated:
        if source.suffix != ".svg":
            continue
        source.write_text(
            "\n".join(line.rstrip() for line in source.read_text().splitlines()) + "\n"
        )


if __name__ == "__main__":
    main()
