"""Rebuild publication architecture figures as editable SVG, vector PDF and PNG.

Run: python scripts/build_architecture_figures.py [--out docs/report/figures]
All geometry, labels and connector routes are authored below. No image assets,
external render service, Graphviz, or GPU are required.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse, FancyArrowPatch, FancyBboxPatch, Polygon
from matplotlib.path import Path as MplPath

plt.switch_backend("Agg")

INK = "#263442"
MUTED = "#607080"
LINE = "#B8C4CE"
BLUE = "#3A6F9D"
TEAL = "#2A8078"
AMBER = "#AB7A31"
PURPLE = "#786295"
PALE = {
    BLUE: "#EEF4F9",
    TEAL: "#EDF6F3",
    AMBER: "#FBF5E9",
    PURPLE: "#F3F0F7",
    MUTED: "#F4F6F8",
}

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 16,
        "svg.fonttype": "none",
        "svg.hashsalt": "simready-architecture-v1",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.linewidth": 0.8,
        "savefig.facecolor": "white",
    }
)


def canvas(title, subtitle):
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 16), ylim=(0, 9), aspect="equal")
    ax.axis("off")
    text(ax, 0.45, 8.55, title, 25, weight="bold")
    text(ax, 0.45, 8.13, subtitle, 14.5, color=MUTED)
    return fig, ax


def text(
    ax,
    x,
    y,
    label,
    size=16,
    *,
    color=INK,
    weight="normal",
    ha="left",
    va="center",
    **kw,
):
    return ax.text(
        x,
        y,
        label,
        fontsize=size,
        color=color,
        fontweight=weight,
        ha=ha,
        va=va,
        linespacing=1.38,
        zorder=6,
        **kw,
    )


def rect(
    ax, x, y, w, h, *, color=MUTED, fill=None, dashed=False, lw=1.0, radius=0.10, z=2
):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=fill or "white",
        edgecolor=color,
        linewidth=lw,
        linestyle=(0, (5, 3)) if dashed else "solid",
        zorder=z,
    )
    ax.add_patch(patch)
    return patch


def panel(ax, x, y, w, h, label, color=MUTED, dashed=False):
    rect(
        ax,
        x,
        y,
        w,
        h,
        color=LINE if not dashed else color,
        dashed=dashed,
        lw=0.9,
        radius=0.12,
        z=0,
    )
    text(ax, x + 0.23, y + h - 0.36, label, 18.5, weight="bold", color=color)


def arrow(ax, points, *, color=INK, dashed=False, lw=1.4, scale=12, z=4):
    path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1))
    patch = FancyArrowPatch(
        path=path,
        arrowstyle="-|>",
        mutation_scale=scale,
        linewidth=lw,
        color=color,
        fill=True,
        linestyle=(0, (4, 3)) if dashed else "solid",
        joinstyle="round",
        capstyle="round",
        zorder=z,
    )
    ax.add_patch(patch)


def line(ax, points, *, color=LINE, lw=1.0, dashed=False, z=4):
    ax.plot(
        *zip(*points, strict=True),
        color=color,
        lw=lw,
        linestyle=(0, (4, 3)) if dashed else "solid",
        zorder=z,
    )


def node(ax, x, y, w, h, title, detail="", color=BLUE, dashed=False, size=18):
    rect(ax, x, y, w, h, color=color, fill=PALE[color], dashed=dashed)
    text(
        ax,
        x + w / 2,
        y + h * (0.65 if detail else 0.5),
        title,
        size,
        color=color,
        weight="bold",
        ha="center",
    )
    if detail:
        text(ax, x + w / 2, y + h * 0.28, detail, 14.5, color=MUTED, ha="center")


def badge(ax, x, y, label, color=BLUE, w=0.48):
    rect(ax, x, y - 0.16, w, 0.32, color=color, fill=color, radius=0.05)
    text(ax, x + w / 2, y, label, 13.5, color="white", weight="bold", ha="center")


def film(ax, x, y, w=1.55, h=0.90, color=BLUE):
    rect(ax, x - w / 2, y - h / 2, w, h, color=color, fill=PALE[color], radius=0.06)
    for side in (-1, 1):
        for i in range(5):
            rect(
                ax,
                x - w / 2 + 0.09 + i * (w - 0.22) / 5,
                y + side * (h / 2 - 0.12) - 0.035,
                0.10,
                0.07,
                color=color,
                fill="white",
                lw=0.5,
                radius=0.01,
            )
    ax.add_patch(
        Polygon(
            [(x - 0.13, y - 0.17), (x - 0.13, y + 0.17), (x + 0.20, y)],
            closed=True,
            facecolor=color,
            edgecolor="none",
            zorder=3,
        )
    )


def frames(ax, x, y):
    for dx, dy in ((0.18, 0.14), (0.09, 0.07), (0, 0)):
        rect(
            ax,
            x - 0.61 + dx,
            y - 0.38 + dy,
            1.22,
            0.76,
            color=BLUE,
            fill="white",
            radius=0.03,
        )
    line(
        ax,
        [
            (x - 0.48, y - 0.2),
            (x - 0.12, y + 0.12),
            (x + 0.08, y - 0.1),
            (x + 0.34, y + 0.08),
            (x + 0.48, y - 0.2),
        ],
        color=BLUE,
    )
    ax.add_patch(
        Circle((x + 0.27, y + 0.20), 0.06, facecolor=AMBER, edgecolor="none", zorder=4)
    )


def gaussian(ax, x, y):
    points = [
        (-0.48, -0.13),
        (-0.31, 0.19),
        (-0.12, -0.22),
        (0.02, 0.13),
        (0.26, 0.23),
        (0.44, -0.11),
        (0.22, -0.30),
        (-0.25, -0.02),
        (0.05, -0.05),
    ]
    for i, (dx, dy) in enumerate(points):
        ax.add_patch(
            Ellipse(
                (x + dx, y + dy),
                0.33,
                0.18,
                angle=(i * 29) % 180,
                facecolor=BLUE,
                edgecolor="none",
                alpha=0.26 + 0.055 * (i % 4),
                zorder=3,
            )
        )


def cabinet(ax, x, y, scale=1.0):
    w, h = 1.05 * scale, 1.45 * scale
    rect(ax, x - w / 2, y - h / 2, w, h, color=AMBER, fill="white", radius=0.025)
    rect(
        ax,
        x - w / 2 + 0.08 * scale,
        y - h / 2 + 0.09 * scale,
        w - 0.16 * scale,
        h - 0.18 * scale,
        color=AMBER,
        fill=PALE[AMBER],
        lw=0.7,
        radius=0.015,
    )
    line(
        ax,
        [(x + 0.27 * scale, y - 0.08 * scale), (x + 0.27 * scale, y + 0.18 * scale)],
        color=AMBER,
        lw=2,
    )
    for dx, col in ((-0.22, TEAL), (0.0, AMBER), (0.22, PURPLE)):
        ax.add_patch(
            Circle(
                (x + dx * scale, y + 0.38 * scale),
                0.055 * scale,
                facecolor=col,
                edgecolor="white",
                lw=0.5,
                zorder=4,
            )
        )
    rect(
        ax,
        x - w / 2 + 0.08 * scale,
        y - h / 2 + 0.09 * scale,
        w - 0.16 * scale,
        0.31 * scale,
        color=AMBER,
        fill="white",
        lw=0.6,
        radius=0.01,
    )
    for xx in (-0.3, -0.12, 0.06, 0.24):
        line(
            ax,
            [
                (x + xx * scale, y - h / 2 + 0.11 * scale),
                (x + (xx + 0.13) * scale, y - h / 2 + 0.37 * scale),
            ],
            color=AMBER,
            lw=0.6,
        )


def sheets(ax, x, y, color=TEAL, w=1.1, h=0.72):
    for offset in (0.14, 0.07, 0):
        rect(
            ax,
            x - w / 2 + offset,
            y - h / 2 + offset,
            w,
            h,
            color=color,
            fill="white",
            radius=0.03,
        )
    for yy in (-0.16, 0.02, 0.20):
        line(ax, [(x - 0.33, y + yy), (x + 0.31, y + yy)], color=color, lw=0.9)


def check(ax, x, y, color=PURPLE, scale=0.2):
    line(
        ax,
        [
            (x - scale * 0.7, y),
            (x - scale * 0.1, y - scale * 0.55),
            (x + scale, y + scale * 0.75),
        ],
        color=color,
        lw=2.1,
        z=4,
    )


def legend_line(ax, x, y, label, color=INK, dashed=False):
    line(ax, [(x, y), (x + 0.38, y)], color=color, dashed=dashed, lw=1.5)
    text(ax, x + 0.5, y, label, 14, color=MUTED)


def overview():
    fig, ax = canvas(
        "Reconstruction-to-interaction overview",
        "Reviewed / offline preparation is distinct from executable scene delivery",
    )
    panel(ax, 0.45, 1.55, 4.65, 6.10, "(a) Source evidence", BLUE)
    panel(ax, 5.65, 1.55, 4.70, 6.10, "(b) Reviewed preparation", AMBER, dashed=True)
    panel(ax, 10.90, 1.55, 4.65, 6.10, "(c) Runnable delivery", TEAL)
    badge(ax, 0.73, 6.84, "M1", BLUE)
    text(ax, 1.33, 6.84, "Register & observe", 17, weight="bold", color=BLUE)
    film(ax, 1.55, 5.95)
    frames(ax, 3.83, 5.95)
    arrow(ax, [(2.36, 5.95), (3.04, 5.95)])
    text(ax, 1.55, 5.25, "Source video", 16, ha="center")
    text(ax, 3.83, 5.25, "Frames + split", 16, ha="center")
    arrow(ax, [(1.55, 4.99), (1.55, 4.16)])
    text(ax, 1.73, 4.55, "video upload", 14, color=MUTED)
    arrow(ax, [(4.54, 5.95), (6.29, 5.95)], color=AMBER, dashed=True)
    text(ax, 5.40, 6.17, "reference views", 14, color=AMBER, ha="center")
    rect(ax, 0.77, 2.90, 4.01, 1.23, color=BLUE, fill=PALE[BLUE])
    gaussian(ax, 1.55, 3.49)
    badge(ax, 2.20, 3.83, "M2", BLUE)
    text(ax, 2.79, 3.83, "Spatial prior", 17, weight="bold", color=BLUE)
    text(ax, 2.20, 3.40, "Aholo · PLY / SPZ", 16)
    text(ax, 2.20, 3.10, "Receipt-bound cloud job", 14, color=MUTED)
    rect(ax, 0.77, 1.91, 4.01, 0.63, color=MUTED, fill=PALE[MUTED], dashed=True)
    text(ax, 0.98, 2.22, "VGGT → gsplat", 16, weight="bold", color=MUTED)
    text(ax, 4.58, 2.22, "research", 14, ha="right", color=MUTED)
    text(ax, 0.72, 1.69, "Frame-based study; not the delivery prior", 14, color=MUTED)
    arrow(
        ax,
        [(4.80, 3.52), (5.93, 3.52), (5.93, 5.13), (6.17, 5.13)],
        color=AMBER,
        dashed=True,
    )
    badge(ax, 5.94, 6.84, "M3", AMBER)
    text(ax, 6.54, 6.84, "Source-bound assets", 17, weight="bold", color=AMBER)
    cabinet(ax, 6.80, 5.43, 0.85)
    text(ax, 7.53, 5.98, "Align + estimate scale", 15.5)
    text(ax, 7.53, 5.51, "Textures + geometry", 15.5)
    text(ax, 7.53, 5.04, "Label unseen surfaces", 15.5)
    text(ax, 8.00, 4.45, "Three-layer USD contract", 17, ha="center", weight="bold")
    rows = [
        (3.72, "Appearance", "Gaussian · mesh · texture", BLUE),
        (3.00, "Collision", "Rigid proxies · limits", TEAL),
        (2.28, "Interaction", "Joints · contact · state", PURPLE),
    ]
    for yy, title, detail, col in rows:
        rect(ax, 6.18, yy, 3.66, 0.62, color=col, fill=PALE[col], lw=0.8, radius=0.04)
        text(ax, 6.36, yy + 0.43, title, 15.5, weight="bold", color=col)
        text(ax, 6.36, yy + 0.16, detail, 14, ha="left", color=MUTED)
    line(ax, [(9.97, 2.28), (10.08, 2.28), (10.08, 4.30), (9.97, 4.30)], color=AMBER)
    arrow(ax, [(8.00, 4.78), (8.00, 4.63)], color=AMBER, scale=10)
    text(
        ax, 8.00, 1.91, "Scene + rig + control manifests", 14, ha="center", color=MUTED
    )
    arrow(ax, [(10.08, 3.29), (10.60, 3.29), (10.60, 6.25), (11.18, 6.25)], color=TEAL)
    text(ax, 13.22, 6.89, "simready run", 18, weight="bold", ha="center", color=TEAL)
    text(
        ax,
        13.22,
        6.55,
        "Reviewed bundle + source video",
        14.5,
        ha="center",
        color=MUTED,
    )
    node(
        ax,
        11.20,
        5.35,
        4.05,
        0.88,
        "M4  Observe + execute",
        "Source checks · Lite3 + Z1 task",
        TEAL,
        size=17,
    )
    arrow(ax, [(13.23, 5.32), (13.23, 4.97)])
    node(
        ax,
        11.20,
        4.03,
        4.05,
        0.88,
        "M5  Independent audit",
        "Measured contact · task evidence",
        PURPLE,
        size=17,
    )
    arrow(ax, [(13.23, 4.00), (13.23, 3.65)])
    node(
        ax,
        11.20,
        2.71,
        4.05,
        0.88,
        "M6  Native replay",
        "Measured trajectory → RTX video",
        TEAL,
        size=17,
    )
    text(
        ax, 13.22, 2.20, "G2: authored-motion demo only", 14.5, ha="center", color=MUTED
    )
    text(
        ax,
        13.22,
        1.86,
        "Task audit ≠ whole-scene certification",
        14,
        ha="center",
        color=MUTED,
    )
    rect(ax, 0.45, 0.63, 15.1, 0.63, color=LINE, fill="#F7F9FA", radius=0.05, lw=0.7)
    text(ax, 0.70, 0.95, "EVIDENCE", 14, weight="bold", color=MUTED)
    text(ax, 2.17, 0.95, "Source hashes + frame IDs", 14.5, color=MUTED)
    text(ax, 6.00, 0.95, "Observed / estimated / unknown", 14.5, color=MUTED)
    text(ax, 11.13, 0.95, "Sensors · events · audit · video", 14.5, color=MUTED)
    for xx in (5.35, 10.60):
        line(ax, [(xx, 0.77), (xx, 1.12)], color=LINE, lw=0.8)
    legend_line(ax, 0.55, 0.28, "implemented", INK)
    legend_line(ax, 3.15, 0.28, "offline / reviewed setup", AMBER, True)
    legend_line(ax, 7.18, 0.28, "research branch", MUTED, True)
    text(
        ax,
        15.45,
        0.28,
        "New-scene asset preparation is not automatic.",
        14,
        ha="right",
        color=MUTED,
    )
    return fig


def scene_map(ax, x, y, w=1.8, h=0.8, defects=False, color=BLUE):
    rect(
        ax, x - w / 2, y - h / 2, w, h, color=color, fill="white", radius=0.025, lw=0.8
    )
    for xx, yy, ww, hh in (
        (-0.33, 0.18, 0.42, 0.18),
        (0.27, 0.18, 0.42, 0.18),
        (-0.36, -0.18, 0.36, 0.17),
        (0.27, -0.18, 0.48, 0.17),
    ):
        rect(
            ax,
            x + xx * w - w * 0.16,
            y + yy * h - h * 0.10,
            ww * w * 0.58,
            hh * h * 1.2,
            color=color,
            fill=PALE[color],
            radius=0.01,
            lw=0.7,
        )
    line(ax, [(x - w * 0.40, y), (x + w * 0.40, y)], color=color, lw=0.7, dashed=True)
    if defects:
        for dx, dy in ((-0.2, 0.13), (0.28, -0.13)):
            ax.add_patch(
                Circle(
                    (x + dx * w, y + dy * h),
                    0.11,
                    facecolor="white",
                    edgecolor=AMBER,
                    lw=1.5,
                    zorder=5,
                )
            )
            text(
                ax,
                x + dx * w,
                y + dy * h,
                "!",
                10.5,
                color=AMBER,
                weight="bold",
                ha="center",
            )


def repair():
    fig, ax = canvas(
        "Evidence-bound repair, under a frozen development protocol",
        "Registered proposals change candidates; independent evaluation decides whether the baseline advances",
    )
    panel(ax, 0.45, 2.06, 3.15, 5.58, "(a) Observe", BLUE)
    panel(ax, 4.03, 2.06, 5.77, 5.58, "(b) Propose & execute", TEAL)
    panel(ax, 10.28, 2.06, 5.27, 5.58, "(c) Independent gate", PURPLE)
    scene_map(ax, 2.02, 6.47, w=1.95, h=0.68, color=BLUE)
    text(ax, 2.02, 5.90, "Current scene Cₖ", 17, weight="bold", ha="center")
    arrow(ax, [(2.02, 5.60), (2.02, 5.24)])
    node(
        ax,
        0.75,
        4.21,
        2.55,
        0.94,
        "Standard views",
        "Fixed views + sources",
        BLUE,
        size=16,
    )
    arrow(ax, [(2.02, 4.18), (2.02, 3.82)])
    rect(ax, 0.75, 2.40, 2.55, 1.34, color=BLUE, fill=PALE[BLUE])
    text(ax, 2.02, 3.42, "Defect ledger Dₖ", 17, weight="bold", ha="center", color=BLUE)
    scene_map(ax, 1.39, 2.84, w=0.91, h=0.60, defects=True, color=BLUE)
    text(ax, 2.45, 2.84, "Type\nEvidence", 14, ha="center", color=MUTED)
    arrow(ax, [(3.32, 3.10), (3.81, 3.10), (3.81, 5.90), (4.36, 5.90)])
    node(
        ax,
        4.37,
        5.20,
        5.08,
        1.37,
        "Agent selects a proposal",
        "Registered ID · version · parameter bounds",
        BLUE,
        size=18,
    )
    text(ax, 6.91, 6.86, "Rules / Codex / API", 14.5, color=MUTED, ha="center")
    arrow(ax, [(5.57, 5.18), (5.57, 4.53)])
    rect(ax, 4.37, 3.40, 2.38, 1.04, color=TEAL, fill=PALE[TEAL])
    text(ax, 5.56, 4.10, "Worker DAG", 17, weight="bold", color=TEAL, ha="center")
    for xx in (4.83, 5.55, 6.27):
        rect(
            ax,
            xx - 0.16,
            3.61,
            0.32,
            0.26,
            color=TEAL,
            fill="white",
            radius=0.025,
            lw=0.9,
        )
    arrow(ax, [(5.01, 3.74), (5.37, 3.74)], color=TEAL, scale=8, lw=1)
    arrow(ax, [(5.73, 3.74), (6.09, 3.74)], color=TEAL, scale=8, lw=1)
    arrow(ax, [(6.77, 3.92), (7.15, 3.92)])
    rect(ax, 7.20, 3.30, 2.25, 1.34, color=TEAL, fill=PALE[TEAL])
    text(ax, 8.325, 4.37, "Candidate C′", 17, weight="bold", color=TEAL, ha="center")
    scene_map(ax, 8.325, 3.96, w=1.39, h=0.42, color=TEAL)
    text(ax, 8.325, 3.48, "Immutable hash", 14, ha="center", color=MUTED)
    text(ax, 7.35, 4.82, "Trusted, budgeted plugins", 14, color=MUTED, ha="center")
    arrow(ax, [(9.47, 3.92), (10.04, 3.92), (10.04, 5.89), (10.61, 5.89)])
    node(
        ax,
        10.62,
        5.20,
        4.61,
        1.37,
        "Frozen-protocol verifier",
        "Fresh process · scorecard + defects",
        PURPLE,
        size=18,
    )
    text(
        ax,
        12.92,
        6.86,
        "Evaluator has no Agent dependency",
        14.5,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(12.93, 5.18), (12.93, 4.75)], color=PURPLE)
    ax.add_patch(
        Polygon(
            [(12.93, 4.70), (14.38, 4.11), (12.93, 3.52), (11.48, 4.11)],
            closed=True,
            facecolor=PALE[PURPLE],
            edgecolor=PURPLE,
            lw=1.1,
            zorder=2,
        )
    )
    text(
        ax,
        12.93,
        4.11,
        "Improved\n+ no regression?",
        14.0,
        weight="bold",
        color=PURPLE,
        ha="center",
    )
    arrow(ax, [(11.48, 4.11), (11.34, 4.11), (11.34, 3.08)], color=TEAL)
    arrow(ax, [(14.38, 4.11), (14.53, 4.11), (14.53, 3.08)], color=MUTED)
    text(ax, 11.37, 3.50, "yes", 14, color=TEAL, ha="left")
    text(ax, 14.57, 3.50, "no", 14, color=MUTED, ha="left")
    node(ax, 10.62, 2.36, 1.91, 0.68, "Accept", color=TEAL, size=17)
    node(ax, 13.01, 2.36, 2.22, 0.68, "Keep baseline", color=MUTED, size=16)
    arrow(
        ax,
        [(11.58, 2.33), (11.58, 1.73), (0.22, 1.73), (0.22, 6.47), (1.35, 6.47)],
        color=TEAL,
    )
    text(ax, 1.15, 1.87, "Advance only on improvement", 14, color=TEAL)
    rect(ax, 4.37, 2.36, 5.08, 0.68, color=AMBER, fill=PALE[AMBER], radius=0.06)
    text(
        ax,
        6.91,
        2.71,
        "Evidence memory + recipe library",
        16,
        weight="bold",
        ha="center",
        color=AMBER,
    )
    arrow(
        ax,
        [(14.12, 2.33), (14.12, 2.18), (10.04, 2.18), (10.04, 2.70), (9.48, 2.70)],
        color=AMBER,
    )
    ax.add_patch(
        Circle((11.58, 2.18), 0.035, facecolor=AMBER, edgecolor="none", zorder=5)
    )
    text(ax, 10.04, 3.02, "episodes", 14, color=AMBER, ha="center")
    arrow(
        ax,
        [(4.37, 2.70), (4.17, 2.70), (4.17, 5.55), (4.36, 5.55)],
        color=AMBER,
        dashed=True,
    )
    text(
        ax, 6.91, 3.15, "All outcomes are evidence-bound", 14, color=MUTED, ha="center"
    )
    rect(ax, 0.45, 0.59, 15.10, 0.72, color=LINE, fill="#F7F9FA", radius=0.05, lw=0.7)
    text(ax, 0.70, 0.95, "REUSE GATE", 14, weight="bold", color=AMBER)
    text(ax, 2.35, 0.95, "Candidate passes", 14.5, color=MUTED)
    text(ax, 5.50, 0.95, "≥2 scenes + validation run", 14.5, color=MUTED)
    text(ax, 10.15, 0.95, "Matching protocol / plugin version", 14.5, color=MUTED)
    for xx in (5.13, 9.78):
        arrow(ax, [(xx - 0.28, 0.95), (xx + 0.10, 0.95)], color=AMBER, scale=10)
    text(
        ax,
        0.55,
        0.23,
        "Budget, no progress or missing evidence → stop",
        14,
        color=MUTED,
    )
    text(
        ax,
        15.45,
        0.23,
        "Development acceptance ≠ automatic release; model weights are unchanged.",
        14,
        ha="right",
        color=MUTED,
    )
    return fig


def spring(ax, x, y, w=0.55, h=0.18, color=AMBER):
    pts = [(x, y)]
    for i in range(8):
        pts.append((x + w * (i + 0.5) / 8, y + (-1 if i % 2 else 1) * h))
    pts.append((x + w, y))
    line(ax, pts, color=color, lw=1.2)


def robot():
    fig, ax = canvas(
        "Task control, device binding and physical evidence",
        "Lite3 + Z1 validated task · high-level navigation and manipulation remain scene-specific",
    )
    rect(ax, 0.45, 6.64, 15.10, 1.03, color=BLUE, fill=PALE[BLUE], radius=0.1)
    text(ax, 0.72, 7.42, "TASK SUPERVISOR", 15, weight="bold", color=BLUE)
    text(ax, 15.25, 7.42, "Measured state transitions", 14, color=MUTED, ha="right")
    states = [
        ("Stand", 1.42),
        ("Navigate", 3.27),
        ("Settle", 5.12),
        ("Reach", 6.97),
        ("Press", 8.82),
        ("Withdraw", 10.67),
        ("Retract", 12.52),
        ("Success", 14.37),
    ]
    for i, (label, x) in enumerate(states):
        ax.add_patch(
            Circle(
                (x, 6.84), 0.055, facecolor=BLUE, edgecolor="white", lw=0.8, zorder=4
            )
        )
        text(ax, x, 7.08, label, 14.5, ha="center", color=BLUE)
        if i < len(states) - 1:
            arrow(
                ax,
                [(x + 0.13, 6.84), (states[i + 1][1] - 0.13, 6.84)],
                color=BLUE,
                lw=1.1,
                scale=9,
            )
    panel(ax, 0.45, 1.78, 4.30, 4.50, "(a) Task controllers", BLUE)
    panel(ax, 5.18, 1.78, 5.24, 4.50, "(b) Device + articulation", TEAL)
    panel(ax, 10.85, 1.78, 4.70, 4.50, "(c) Physics + evidence", PURPLE)
    node(
        ax,
        0.76,
        4.46,
        3.68,
        0.91,
        "Known-map navigation",
        "Measured base pose → ONNX gait",
        BLUE,
        size=17,
    )
    node(
        ax,
        0.76,
        3.25,
        3.68,
        0.91,
        "RGB-D target localization",
        "Unique image target → 3D surface",
        BLUE,
        size=17,
    )
    node(
        ax,
        0.76,
        2.04,
        3.68,
        0.91,
        "FK / IK + collision checks",
        "Bounded joint path + press control",
        BLUE,
        size=17,
    )
    arrow(ax, [(2.60, 3.22), (2.60, 2.98)], color=BLUE, scale=10)
    line(ax, [(4.46, 4.91), (4.94, 4.91), (4.94, 2.49), (4.46, 2.49)], color=BLUE)
    arrow(ax, [(4.94, 4.91), (5.48, 4.91)], color=BLUE)
    rect(ax, 5.49, 4.30, 4.62, 1.10, color=TEAL, fill=PALE[TEAL])
    text(ax, 7.80, 5.13, "RobotInterface", 19, weight="bold", ha="center", color=TEAL)
    text(ax, 7.80, 4.78, "describe · observe · command", 14.5, ha="center", color=MUTED)
    text(
        ax, 7.80, 4.47, "Joint position / effort targets", 14, ha="center", color=MUTED
    )
    text(
        ax,
        7.80,
        5.72,
        "Joint order · units · tensor shape",
        14.5,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(7.80, 4.27), (7.80, 3.94)], color=TEAL)
    rect(ax, 5.49, 2.04, 4.62, 1.82, color=TEAL, fill=PALE[TEAL], radius=0.08)
    text(
        ax,
        7.80,
        3.61,
        "19-DOF combined articulation",
        16,
        weight="bold",
        ha="center",
        color=TEAL,
    )
    # Free-base quadruped body and four articulated legs.
    rect(ax, 6.03, 2.87, 1.10, 0.26, color=TEAL, fill="white", radius=0.035, lw=1.2)
    for xx, shift in ((6.15, -0.18), (6.37, 0.04), (6.84, -0.04), (7.02, 0.18)):
        line(
            ax,
            [(xx, 2.87), (xx + shift, 2.64), (xx + shift - 0.08, 2.40)],
            color=TEAL,
            lw=1.4,
        )
        ax.add_patch(
            Circle(
                (xx + shift, 2.64),
                0.035,
                facecolor="white",
                edgecolor=TEAL,
                lw=0.8,
                zorder=5,
            )
        )
    # Mounted serial arm; passive spring is mechanically attached at its tip.
    arm = [(6.82, 3.13), (7.30, 3.20), (7.83, 2.82), (8.43, 3.18), (8.95, 3.03)]
    line(ax, arm, color=TEAL, lw=2.0)
    for xx, yy in arm[:-1]:
        ax.add_patch(
            Circle((xx, yy), 0.055, facecolor="white", edgecolor=TEAL, lw=1.2, zorder=5)
        )
    spring(ax, 8.98, 3.03, w=0.60, h=0.10)
    line(ax, [(9.59, 2.89), (9.59, 3.17)], color=AMBER, lw=1.6)
    text(ax, 6.49, 2.22, "Lite3 · 12", 14.5, color=TEAL, ha="center")
    text(ax, 8.01, 2.22, "Z1 · 6", 14.5, color=TEAL, ha="center")
    text(ax, 9.29, 2.22, "Spring · 1", 14.5, color=AMBER, ha="center")
    arrow(ax, [(10.13, 2.97), (10.64, 2.97), (10.64, 4.91), (11.15, 4.91)], color=TEAL)
    node(
        ax,
        11.16,
        4.30,
        4.08,
        1.10,
        "PhysX robot + scene",
        "Contacts · displacement · state",
        TEAL,
        size=18,
    )
    arrow(ax, [(13.20, 4.27), (13.20, 3.91)], color=PURPLE)
    rect(ax, 11.16, 3.15, 4.08, 0.68, color=PURPLE, fill=PALE[PURPLE])
    text(
        ax,
        13.20,
        3.49,
        "Frozen traces + RGB-D + replay",
        15.5,
        ha="center",
        color=PURPLE,
    )
    arrow(ax, [(12.13, 3.12), (12.13, 2.81)], color=PURPLE)
    arrow(ax, [(14.25, 3.12), (14.25, 2.81)], color=PURPLE)
    node(ax, 11.16, 2.05, 1.93, 0.69, "Audit", color=PURPLE, size=17)
    node(ax, 13.31, 2.05, 1.93, 0.69, "RTX replay", color=TEAL, size=16)
    arrow(ax, [(13.10, 2.40), (13.30, 2.40)], color=PURPLE, scale=9)
    text(ax, 12.15, 1.91, "20 task checks", 14, ha="center", color=MUTED)
    text(ax, 14.26, 1.91, "Audit pass only", 14, ha="center", color=MUTED)
    arrow(
        ax,
        [(15.27, 4.91), (15.77, 4.91), (15.77, 1.32), (2.60, 1.32), (2.60, 1.73)],
        color=TEAL,
    )
    text(
        ax,
        8.35,
        1.48,
        "Measured articulation state + contact feedback",
        15,
        color=TEAL,
        ha="center",
    )
    arrow(ax, [(2.60, 6.61), (2.60, 6.31)], color=BLUE, scale=11)
    text(
        ax,
        0.56,
        0.78,
        "Physical port: joint_drive only; no task-time root-pose writes.",
        14.5,
        color=MUTED,
    )
    text(
        ax,
        0.56,
        0.35,
        "Feedback uses native runtime buffers; port.observe exposes the same state contract.",
        14,
        color=MUTED,
    )
    text(
        ax,
        15.45,
        0.78,
        "G2 root_pose is a separate kinematic adapter.",
        14.5,
        color=MUTED,
        ha="right",
    )
    text(
        ax,
        15.45,
        0.35,
        "Task audit does not certify all scene assets.",
        14,
        color=MUTED,
        ha="right",
    )
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs/report/figures",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, builder in (
        ("architecture_overview", overview),
        ("repair_closed_loop", repair),
        ("robot_control_runtime", robot),
    ):
        fig = builder()
        for suffix in ("svg", "pdf", "png"):
            metadata = {"Creator": "SimReady native architecture figure builder"}
            if suffix == "pdf":
                metadata.update(CreationDate=None, ModDate=None)
            elif suffix == "svg":
                metadata["Date"] = None
            output = args.out / f"{name}.{suffix}"
            fig.savefig(output, dpi=240, metadata=metadata)
            if suffix == "svg":
                output.write_text(
                    "\n".join(line.rstrip() for line in output.read_text().splitlines())
                    + "\n"
                )
        plt.close(fig)
        print(f"{name}: 16 x 9 in; SVG/PDF vector; PNG 3840 x 2160")


if __name__ == "__main__":
    main()
