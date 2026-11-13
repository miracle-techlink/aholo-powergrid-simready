"""Draw the new-video construction path as editable vectors and a PNG."""

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


def build():
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "svg.fonttype": "none",
            "svg.hashsalt": "simready-new-video-pipeline-v1",
            "pdf.fonttype": 42,
        }
    )
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 16), ylim=(0, 9))
    ax.axis("off")

    def text(x, y, value, size=15, color=INK, weight="normal", **kwargs):
        ax.text(
            x,
            y,
            value,
            fontsize=size,
            color=color,
            weight=weight,
            ha="left",
            va="center",
            **kwargs,
        )

    def box(x, y, w, h, color, fill="white", dashed=False):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.025,rounding_size=0.07",
                facecolor=fill,
                edgecolor=color,
                linewidth=1.1,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )

    def arrow(points, color=INK, dashed=False):
        for a, b in zip(points[:-2], points[1:-1], strict=False):
            ax.plot(
                [a[0], b[0]],
                [a[1], b[1]],
                color=color,
                lw=1.35,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        ax.add_patch(
            FancyArrowPatch(
                points[-2],
                points[-1],
                arrowstyle="-|>",
                mutation_scale=13,
                color=color,
                linewidth=1.35,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )

    text(0.4, 8.6, "New video to a source-bound scene candidate", 26, weight="bold")
    text(
        0.4,
        8.17,
        "Hashes bind source declarations; geometric fidelity and robot task success require separate validation",
        14.5,
        MUTED,
    )
    for x, w, title, color in (
        (0.35, 4.55, "(a) Host: bind the inputs", BLUE),
        (5.25, 5.15, "(b) Worker: construct the scene", AMBER),
        (10.75, 4.9, "(c) Independently inspect + deliver", TEAL),
    ):
        box(x, 1.6, w, 6.13, "#bcc8d3")
        text(x + 0.19, 7.42, title, 16.3, color, "bold")

    box(0.6, 6.18, 4.05, 0.79, BLUE, "#f0f5fa")
    text(0.8, 6.68, "Source video", 17, BLUE, "bold")
    text(0.8, 6.36, "Original bytes + source file hash", 14)
    arrow([(2.625, 6.15), (2.625, 5.87)])
    box(0.6, 5.04, 4.05, 0.8, BLUE, "#f0f5fa")
    text(0.8, 5.54, "Observation manifest", 17, BLUE, "bold")
    text(0.8, 5.22, "Frames + nominal sampling times", 14)
    arrow([(4.68, 6.58), (4.79, 6.58), (4.79, 4.02), (4.68, 4.02)], BLUE)
    arrow([(0.57, 5.43), (0.46, 5.43), (0.46, 2.44), (0.57, 2.44)], BLUE)
    box(0.6, 3.22, 4.05, 1.48, BLUE)
    text(0.8, 4.42, "Reconstruction stage", 17, BLUE, "bold")
    text(0.8, 4.02, "Raw video: cloud submit / job resume", 13.5)
    text(0.8, 3.69, "or source-declared artifact manifest", 14)
    text(0.8, 3.40, "PLY + hashes + receipt", 14, MUTED)
    arrow([(2.625, 3.19), (2.625, 2.91)])
    box(0.6, 1.99, 4.05, 0.9, BLUE, "#f0f5fa")
    text(0.8, 2.59, "worker_inputs", 17, BLUE, "bold")
    text(0.8, 2.22, "Bound PLY + scene spec + source record", 13.3)
    arrow([(4.68, 2.44), (5.07, 2.44), (5.07, 6.55), (5.47, 6.55)], BLUE)

    box(5.5, 6.08, 4.65, 0.95, AMBER, "#fbf7ef")
    text(5.7, 6.7, "Visible Gaussian appearance", 17, AMBER, "bold")
    text(5.7, 6.32, "Identity or declared Sim(3) transform", 14)
    arrow([(7.825, 6.05), (7.825, 5.76)])
    box(5.5, 4.79, 4.65, 0.94, AMBER, "#fbf7ef")
    text(5.7, 5.4, "Bounded voxel collision proxies", 16.3, AMBER, "bold")
    text(5.7, 5.03, "Estimated occupancy; no surface truth", 14)
    arrow([(7.825, 4.76), (7.825, 4.47)])
    box(5.5, 3.52, 4.65, 0.92, AMBER, "#fbf7ef")
    text(5.7, 4.11, "USD layers + preparation record", 16.3, AMBER, "bold")
    text(5.7, 3.75, "Objects, transforms, provenance, limits", 14)
    box(5.5, 1.99, 4.65, 1.12, AMBER, dashed=True)
    text(5.7, 2.85, "Optional explicit scene specification", 15.5, AMBER, "bold")
    text(5.7, 2.52, "Declared floor; box + prismatic button", 13.5)
    text(5.7, 2.20, "Scale evidence; camera + render settings", 13.5)
    arrow([(7.825, 3.14), (7.825, 3.49)], AMBER, dashed=True)
    arrow([(10.18, 3.98), (10.56, 3.98), (10.56, 6.55), (10.97, 6.55)], TEAL)

    box(11, 6.01, 4.4, 1.1, TEAL, "#eef7f5")
    text(11.2, 6.79, "Independent structural audit", 16.3, TEAL, "bold")
    text(11.2, 6.39, "Read exported USD + receipts anew", 14)
    text(11.2, 6.13, "Layers, joints, finite bounds, bindings", 13.4)
    arrow([(13.2, 5.98), (13.2, 5.65)], TEAL)
    box(11, 4.51, 4.4, 1.11, TEAL, "#eef7f5")
    text(11.2, 5.31, "Native camera survey", 17, TEAL, "bold")
    text(11.2, 4.94, "Camera path + frames + encoded video", 13.6)
    text(11.2, 4.66, "Skipped by --prepare-only", 13.4, MUTED)
    arrow([(13.2, 4.48), (13.2, 4.16)], TEAL)
    box(11, 2.69, 4.4, 1.44, TEAL, "#eef7f5")
    text(11.2, 3.82, "Delivery receipt", 17, TEAL, "bold")
    text(11.2, 3.43, "CPU: SCENE_EXPORTED", 14.5)
    text(11.2, 3.12, "Survey: SCENE_SURVEY_READY", 14.5)
    text(11.2, 2.84, "Artifact hashes + actual verification scope", 13.2)
    text(11.05, 2.18, "Robot task validated: false", 15, TEAL, "bold")

    box(0.35, 0.36, 15.3, 0.94, "#c8d2dc", "#f5f7fa")
    text(0.55, 1.03, "FAILURE / RESUME", 14, MUTED, "bold")
    text(3.00, 1.03, "Stop + retain evidence", 14)
    text(7.03, 1.03, "Reuse only matching run identity + artifact hashes", 14)
    text(0.55, 0.65, "No scale anchor: unverified", 13.5, MUTED)
    text(5.65, 0.65, "Unknown up: no floor guessed", 13.5, MUTED)
    text(10.75, 0.65, "Structural pass is not task success", 13.1, MUTED)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    stem = OUTPUT / "new_video_pipeline"
    fig.savefig(stem.with_suffix(".svg"), metadata={"Date": None})
    fig.savefig(
        stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None}
    )
    fig.savefig(stem.with_suffix(".png"), dpi=240)
    plt.close(fig)
    print(stem)


if __name__ == "__main__":
    build()
