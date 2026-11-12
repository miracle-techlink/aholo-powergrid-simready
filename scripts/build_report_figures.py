"""Rebuild the report's vector figures from published DOT, CSV and evidence."""

import csv
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / "docs/report"
    figures = root / "figures"
    for source in sorted(figures.glob("*.dot")):
        for extension in ("svg", "pdf"):
            subprocess.run(
                [
                    "dot",
                    f"-T{extension}",
                    str(source),
                    "-o",
                    str(source.with_suffix(f".{extension}")),
                ],
                check=True,
            )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with (root / "contact_trace.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    figure, axes = plt.subplots(
        3, 1, figsize=(10, 5), sharex=True, constrained_layout=True
    )
    keys = ("target_contact_N", "target_depth_m", "tool_compression_m")
    labels = ("Target contact (N)", "Button travel (mm)", "Tool compression (mm)")
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
    axes[-1].set_xlabel("Simulation time (s)")
    for extension in ("pdf", "svg"):
        figure.savefig(figures / f"contact_trace.{extension}")
    plt.close(figure)
    values = json.loads((root / "evidence.json").read_text())[
        "historical_engineering_wall_seconds"
    ]
    figure, axis = plt.subplots(figsize=(10, 3.4), constrained_layout=True)
    axis.barh(
        list(values),
        list(values.values()),
        color=["#2176ae", "#2b9365", "#b56f23", "#7699ae", "#86988e"],
    )
    axis.invert_yaxis()
    axis.set_xlabel(f"Wall time (s); total {sum(values.values()):.2f} s")
    axis.set_xlim(0, 1550)
    axis.spines[["top", "right"]].set_visible(False)
    for index, value in enumerate(values.values()):
        axis.text(value + 10, index, f"{value:.2f} s", va="center", fontsize=9)
    for extension in ("pdf", "svg"):
        figure.savefig(figures / f"timing.{extension}")


if __name__ == "__main__":
    main()
