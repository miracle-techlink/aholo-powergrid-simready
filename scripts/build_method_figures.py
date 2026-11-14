"""Reproducible, editable method diagrams for the ten-module technical report.

Run: python scripts/build_method_figures.py [--out docs/report/figures]
Each 7.2 x 5.2 inch canvas is designed to remain legible at 16 cm width.
Curves and objects are explanatory schematics, not experimental measurements.
Only Matplotlib vector primitives are used; no image assets, GPU or network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import (
    Arc,
    Circle,
    Ellipse,
    FancyArrowPatch,
    FancyBboxPatch,
    Polygon,
    Rectangle,
)
from matplotlib.path import Path as MplPath

plt.switch_backend("Agg")

INK = "#253443"
GRAY = "#617180"
LINE = "#C8D2DB"
BLUE = "#356D9D"
TEAL = "#237F74"
AMBER = "#B07B29"
PURPLE = "#796196"
RED = "#B65C4C"
PALE = {
    BLUE: "#EAF2F8",
    TEAL: "#EAF5F1",
    AMBER: "#FBF3E6",
    PURPLE: "#F2EDF7",
    GRAY: "#F3F5F7",
}
WIDTH, HEIGHT = 7.2, 5.2

plt.rcParams.update(
    {
        "font.family": ["Noto Sans CJK SC", "DejaVu Sans"],
        "font.size": 11,
        "svg.fonttype": "none",
        "svg.hashsalt": "simready-methods-v1",
        # Embed CJK OpenType/CFF glyphs as vector outlines; Type 42 incorrectly
        # declares them as TrueType and drops Chinese in common PDF viewers.
        # SVG keeps editable text independently of this PDF-only setting.
        "pdf.fonttype": 3,
        "ps.fonttype": 3,
        "savefig.facecolor": "white",
        "mathtext.fontset": "dejavusans",
    }
)


def text(
    ax,
    x,
    y,
    label,
    size=11,
    *,
    color=INK,
    weight="normal",
    ha="left",
    va="center",
    **kwargs,
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
        linespacing=1.25,
        zorder=10,
        **kwargs,
    )


def line(ax, points, color=LINE, lw=1, dashed=False, z=3):
    ax.plot(
        *zip(*points, strict=True),
        color=color,
        lw=lw,
        linestyle=(0, (3, 2)) if dashed else "solid",
        zorder=z,
    )


def arrow(ax, points, color=INK, lw=1.1, dashed=False, scale=8):
    path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1))
    ax.add_patch(
        FancyArrowPatch(
            path=path,
            arrowstyle="-|>",
            mutation_scale=scale,
            linewidth=lw,
            color=color,
            zorder=5,
            linestyle=(0, (3, 2)) if dashed else "solid",
        )
    )


def box(ax, x, y, w, h, color=LINE, fill="white", radius=0.05, dashed=False, lw=0.8):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        edgecolor=color,
        facecolor=fill,
        linewidth=lw,
        linestyle=(0, (3, 2)) if dashed else "solid",
        zorder=1,
    )
    ax.add_patch(patch)
    return patch


def panel(ax, x, y, w, h, letter, title, color=BLUE):
    box(ax, x, y, w, h)
    text(
        ax, x + 0.14, y + h - 0.24, f"{letter}  {title}", 12, weight="bold", color=color
    )


def canvas(title, note):
    fig, ax = plt.subplots(figsize=(WIDTH, HEIGHT))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, WIDTH), ylim=(0, HEIGHT), aspect="equal")
    ax.axis("off")
    text(ax, 0.22, 4.97, title, 14, weight="bold")
    text(ax, WIDTH / 2, 0.23, note, 11, color=GRAY, ha="center")
    return fig, ax


def frame(ax, x, y, w=0.52, h=0.38, color=BLUE, faded=False, roi=False):
    fill = PALE[GRAY] if faded else PALE.get(color, "white")
    box(ax, x, y, w, h, color=LINE if faded else color, fill=fill, radius=0.02)
    ink = LINE if faded else color
    line(
        ax,
        [
            (x + w * 0.10, y + h * 0.15),
            (x + w * 0.43, y + h * 0.63),
            (x + w * 0.69, y + h * 0.23),
            (x + w * 0.91, y + h * 0.5),
        ],
        ink,
        0.8,
    )
    ax.add_patch(Circle((x + w * 0.77, y + h * 0.77), w * 0.045, color=ink, zorder=4))
    if roi:
        ax.add_patch(
            Rectangle(
                (x + w * 0.48, y + h * 0.20),
                w * 0.28,
                h * 0.55,
                edgecolor=AMBER,
                facecolor="none",
                linewidth=1.3,
                zorder=5,
            )
        )


def camera(ax, x, y, size=0.22, color=BLUE, facing=1):
    box(
        ax,
        x - size / 2,
        y - size * 0.35,
        size,
        size * 0.7,
        color=color,
        fill=PALE.get(color, "white"),
        radius=0.02,
    )
    base = x + facing * size / 2
    ax.add_patch(
        Polygon(
            [
                (base, y - size * 0.22),
                (base + facing * size * 0.36, y - size * 0.4),
                (base + facing * size * 0.36, y + size * 0.4),
                (base, y + size * 0.22),
            ],
            closed=True,
            facecolor=PALE.get(color, "white"),
            edgecolor=color,
            lw=0.8,
            zorder=4,
        )
    )


def check(ax, x, y, color=TEAL, size=0.12):
    line(
        ax,
        [
            (x - size * 0.55, y),
            (x - size * 0.1, y - size * 0.45),
            (x + size * 0.65, y + size * 0.5),
        ],
        color,
        1.5,
        z=8,
    )


def cross(ax, x, y, color=GRAY, size=0.07):
    line(ax, [(x - size, y - size), (x + size, y + size)], color, 1.1, z=8)
    line(ax, [(x - size, y + size), (x + size, y - size)], color, 1.1, z=8)


def ellipsoid(ax, x, y, w=0.8, h=0.42, angle=25, color=BLUE):
    for scale, alpha in [(1.0, 0.13), (0.68, 0.15), (0.34, 0.22)]:
        ax.add_patch(
            Ellipse(
                (x, y),
                w * scale,
                h * scale,
                angle=angle,
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                lw=0.8,
                zorder=3,
            )
        )
    ax.add_patch(Circle((x, y), 0.025, color=color, zorder=6))


def axes(ax, x, y, scale=0.5, angle=0, labels=True):
    theta = np.deg2rad(angle)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    for vector, name, color in [
        ([scale, 0], "x", BLUE),
        ([-scale * 0.38, -scale * 0.45], "y", TEAL),
        ([0, scale], "z", PURPLE),
    ]:
        end = np.array([x, y]) + rot @ vector
        arrow(ax, [(x, y), tuple(end)], color, scale=6)
        if labels:
            text(ax, end[0] + 0.045, end[1] + 0.03, name, 11, color=color)


def cube(ax, x, y, size=0.35, color=AMBER, fill=True):
    dx, dy = size * 0.36, size * 0.28
    front = [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]
    top = [
        (x, y + size),
        (x + dx, y + size + dy),
        (x + size + dx, y + size + dy),
        (x + size, y + size),
    ]
    side = [
        (x + size, y),
        (x + size + dx, y + dy),
        (x + size + dx, y + size + dy),
        (x + size, y + size),
    ]
    for points, alpha in [(front, 0.12), (top, 0.07), (side, 0.2)]:
        ax.add_patch(
            Polygon(
                points,
                closed=True,
                facecolor=color if fill else "white",
                alpha=alpha if fill else 1,
                edgecolor="none",
                zorder=3,
            )
        )
        line(ax, points + [points[0]], color, 0.8)


def input_gaussians():
    fig, ax = canvas(
        "从视频采样到高斯外观",
        "原视频上传至 Aholo；抽帧用于来源参考与检查。",
    )
    panel(ax, 0.22, 2.72, 3.26, 1.98, "a", "抽帧与来源登记")
    panel(ax, 3.68, 2.72, 3.30, 1.98, "b", "高斯参数", TEAL)
    panel(ax, 0.22, 0.55, 6.76, 1.95, "c", "投影、着色与合成", PURPLE)
    text(ax, 0.43, 4.13, "名义时间采样网格", color=GRAY)
    xs = np.linspace(0.48, 3.17, 6)
    line(ax, [(0.45, 3.55), (3.20, 3.55)], LINE)
    for index, x in enumerate(xs):
        frame(ax, x - 0.18, 3.68, 0.37, 0.28, faded=index in [1, 3])
        ax.add_patch(Circle((x, 3.55), 0.027, color=LINE if index in [1, 3] else BLUE))
        (cross if index in [1, 3] else check)(ax, x, 3.35)
    text(ax, 1.85, 3.08, "清晰度与哈希筛选", ha="center")
    text(ax, 1.85, 2.84, "训练帧 / 同源视频留出帧", ha="center", color=GRAY)
    ellipsoid(ax, 4.38, 3.73, 1.0, 0.46, 30, TEAL)
    arrow(ax, [(4.38, 3.73), (4.84, 3.99)], TEAL)
    text(ax, 4.12, 3.24, r"$\mu,\;q_{wxyz}$", color=TEAL)
    text(ax, 5.07, 4.06, r"$s=\exp(\ell_s)$")
    text(ax, 5.07, 3.69, r"$\alpha=\sigma(\ell_\alpha)$")
    text(ax, 5.07, 3.30, "SH 阶数 0–3")
    text(
        ax,
        5.33,
        2.96,
        r"$\Sigma=R(q)\,\mathrm{diag}(s^2)\,R(q)^\top$",
        11,
        ha="center",
        color=GRAY,
    )
    camera(ax, 0.64, 1.40, 0.34, PURPLE)
    ellipsoid(ax, 1.51, 1.55, 0.60, 0.34, 25, BLUE)
    ellipsoid(ax, 1.79, 1.25, 0.45, 0.26, -25, TEAL)
    ax.add_patch(
        Polygon(
            [(2.43, 0.94), (2.70, 1.10), (2.70, 1.91), (2.43, 1.74)],
            facecolor=PALE[PURPLE],
            edgecolor=PURPLE,
            lw=0.8,
            zorder=1,
        )
    )
    for point in [(2.56, 1.72), (2.56, 1.18)]:
        line(ax, [(0.85, 1.40), point], LINE, 0.8, dashed=True)
    ellipsoid(ax, 2.55, 1.52, 0.16, 0.39, -8, BLUE)
    text(ax, 1.44, 0.79, "三维高斯", ha="center")
    text(ax, 2.67, 0.79, "二维投影斑点", ha="center")
    text(
        ax,
        3.30,
        1.77,
        r"$\mathrm{SH}_i(v)=\sum_{\ell,m}\beta_{i,\ell m}Y_{\ell m}(v)$",
        12,
    )
    text(ax, 3.30, 1.42, "SH 基函数 → 视角相关颜色", color=GRAY)
    text(ax, 3.30, 1.02, r"$C=\sum_i T_i a_i c_i,\quad T_i=\prod_{j<i}(1-a_j)$", 11)
    text(ax, 3.30, 0.71, "投影不透明度 · 由前至后合成", color=GRAY)
    return fig


def registration_geometry():
    fig, ax = canvas(
        "坐标配准与三层场景准备",
        "来源外观、估计碰撞与显式声明对象分层记录。",
    )
    panel(ax, 0.22, 2.94, 6.76, 1.76, "a", "正尺度 Sim3 与明确的世界坐标系")
    panel(ax, 0.22, 0.55, 3.26, 2.17, "b", "有界碰撞估计", AMBER)
    panel(ax, 3.68, 0.55, 3.30, 2.17, "c", "三个运行层", TEAL)
    axes(ax, 0.88, 3.55, 0.43, 30)
    axes(ax, 3.67, 3.48, 0.48, 0)
    rng = np.random.default_rng(13)
    points = rng.normal(size=(24, 2)) * [0.14, 0.10]
    ax.scatter(points[:, 0] + 1.14, points[:, 1] + 3.90, s=5, c=BLUE, alpha=0.55)
    ax.scatter(
        points[:, 0] * 1.25 + 3.98, points[:, 1] * 1.25 + 3.84, s=5, c=TEAL, alpha=0.55
    )
    arrow(ax, [(1.64, 3.72), (3.12, 3.72)], BLUE, lw=1.3)
    text(ax, 2.36, 4.08, r"$x_w=sRx_s+t$", 12, ha="center")
    text(ax, 0.94, 3.13, "源坐标系", ha="center", color=GRAY)
    text(ax, 3.91, 3.06, "Z 轴向上的世界坐标", ha="center", color=GRAY)
    text(ax, 4.75, 4.12, r"$s>0,\quad R^\top R=I$")
    text(ax, 4.75, 3.79, "尺度需要来源证据")
    box(ax, 4.75, 3.08, 1.96, 0.42, AMBER, PALE[AMBER], dashed=True)
    text(ax, 5.73, 3.29, "否则：尺度未验证", color=AMBER, ha="center")
    # A finite observed floor and a bounded set of occupancy cells.
    line(ax, [(0.48, 1.61), (1.63, 1.74)], AMBER, 1.3)
    samples = rng.uniform([0.5, 1.65], [1.62, 2.10], (34, 2))
    samples[:18, 1] = 1.61 + (samples[:18, 0] - 0.48) * 0.113 + rng.normal(0, 0.015, 18)
    ax.scatter(samples[:, 0], samples[:, 1], s=5, c=GRAY, alpha=0.65)
    arrow(ax, [(1.75, 1.84), (2.05, 1.84)], AMBER)
    for x, y in [(2.18, 1.55), (2.53, 1.55), (2.53, 1.90)]:
        cube(ax, x, y, 0.31)
    text(ax, 1.85, 1.23, r"地面：$n^\top x+d=0$", ha="center")
    text(ax, 1.85, 0.94, "采样 ≤10万点 · 代理 ≤2000个", ha="center")
    text(ax, 1.85, 0.69, "默认上限；超限时合并体素", ha="center", color=GRAY)
    for y, title, detail, color in [
        (2.04, "外观层", "来源高斯外观", BLUE),
        (1.44, "碰撞层", "静态碰撞代理", AMBER),
        (0.85, "交互层", "显式声明的对象", TEAL),
    ]:
        if title == "外观层":
            ellipsoid(ax, 4.17, y + 0.06, 0.44, 0.26, 25, BLUE)
        elif title == "碰撞层":
            cube(ax, 3.96, y - 0.08, 0.31, AMBER, fill=False)
        else:
            box(ax, 3.95, y - 0.07, 0.40, 0.23, TEAL, PALE[TEAL])
            line(
                ax,
                [
                    (4.04, y + 0.16),
                    (4.04, y + 0.25),
                    (4.27, y + 0.25),
                    (4.27, y + 0.16),
                ],
                TEAL,
            )
        text(ax, 4.59, y + 0.14, title, weight="bold", color=color)
        text(ax, 4.59, y - 0.10, detail, color=GRAY)
    return fig


def button_contact():
    fig, ax = canvas(
        "物理原语与单独绑定的接触逻辑",
        "事件需显式绑定；缺样会清空计数；观测器不改写位姿。",
    )
    panel(ax, 0.22, 2.74, 3.26, 1.96, "a", "显式构建的弹簧原语", TEAL)
    panel(ax, 3.68, 2.74, 3.30, 1.96, "b", "适配器的实测输入", BLUE)
    panel(
        ax,
        0.22,
        0.55,
        6.76,
        1.97,
        "c",
        "接触状态观测：触发、释放、重新就绪",
        PURPLE,
    )
    ax.add_patch(
        Rectangle(
            (0.53, 3.30),
            0.24,
            0.75,
            facecolor=PALE[GRAY],
            edgecolor=GRAY,
            hatch="////",
            lw=0.8,
        )
    )
    spring = [(0.77, 3.67), (0.94, 3.67)]
    for i in range(9):
        spring.append((0.98 + i * 0.077, 3.67 + (0.13 if i % 2 == 0 else -0.13)))
    spring += [(1.67, 3.67), (1.85, 3.67)]
    line(ax, spring, TEAL, 1.25)
    box(ax, 1.85, 3.34, 0.24, 0.66, TEAL, PALE[TEAL], radius=0.08, lw=1.1)
    line(ax, [(0.77, 3.24), (2.12, 3.24)], LINE)
    line(ax, [(0.77, 3.17), (0.77, 3.31)], LINE)
    line(ax, [(2.12, 3.17), (2.12, 3.31)], LINE)
    arrow(ax, [(2.10, 4.14), (1.50, 4.14)], TEAL)
    text(ax, 1.79, 4.25, r"压入深度 $d$", color=TEAL, ha="center")
    box(ax, 2.57, 3.48, 0.57, 0.38, BLUE, PALE[BLUE], radius=0.04)
    arrow(ax, [(2.57, 3.67), (2.13, 3.67)], BLUE, lw=1.4)
    text(ax, 2.83, 4.05, "工具接触力", ha="center", color=BLUE)
    ax.add_patch(Circle((2.10, 3.67), 0.04, color=AMBER, zorder=8))
    text(ax, 1.85, 2.99, r"$F_s=-kd-b\dot d$", 12, ha="center")
    # Illustrative normalized sensor traces, not measured task data.
    x0, y0, w, h = 4.10, 3.24, 2.53, 0.70
    threshold_y = y0 + h * 0.56
    line(ax, [(x0, y0), (x0 + w, y0)], GRAY, 0.8)
    line(ax, [(x0, y0), (x0, y0 + h)], GRAY, 0.8)
    line(ax, [(x0, threshold_y), (x0 + w, threshold_y)], GRAY, 0.8, dashed=True)
    times = np.array([0, 10, 15, 25, 30, 45, 50, 60, 75, 90, 110, 120, 130, 145, 160])
    depth = np.array(
        [
            0,
            0.10,
            0.9,
            1.20,
            0.2,
            0.22,
            1.15,
            1.35,
            1.4,
            1.45,
            1.4,
            1.1,
            0.20,
            0.08,
            0.06,
        ]
    )
    force = np.array(
        [
            0,
            0.08,
            1.15,
            0.8,
            0.15,
            0.25,
            1.25,
            1.2,
            1.5,
            1.3,
            1.45,
            0.9,
            0.15,
            0.07,
            0.04,
        ]
    )
    ax.add_patch(
        Rectangle(
            (x0 + w * 50 / 160, y0),
            w * 40 / 160,
            h,
            facecolor=TEAL,
            alpha=0.08,
            edgecolor="none",
            zorder=2,
        )
    )
    ax.plot(x0 + times / 160 * w, y0 + depth * h * 0.56, color=TEAL, lw=1.25, zorder=6)
    ax.plot(x0 + times / 160 * w, y0 + force * h * 0.56, color=BLUE, lw=1.25, zorder=6)
    text(ax, x0 - 0.11, threshold_y, "1", ha="right", color=GRAY)
    for t in [0, 80, 160]:
        text(ax, x0 + t / 160 * w, y0 - 0.16, str(t), ha="center", color=GRAY)
    event_x = x0 + w * 90 / 160
    arrow(ax, [(event_x, 4.18), (event_x, 4.00)], PURPLE)
    text(ax, 5.38, 4.23, "持续 40 ms → 触发", color=PURPLE, ha="center")
    text(ax, 5.33, 2.88, "归一化曲线 · ms · 原理示意", ha="center", color=GRAY)
    line(ax, [(3.98, 4.03), (4.18, 4.03)], TEAL, 1.4)
    text(ax, 4.24, 4.03, "深度", color=TEAL)
    line(ax, [(6.05, 4.03), (6.23, 4.03)], BLUE, 1.4)
    text(ax, 6.29, 4.03, "接触力", color=BLUE)
    for x, label in [(1.09, "允许触发"), (6.08, "等待释放")]:
        box(ax, x - 0.65, 1.08, 1.30, 0.56, PURPLE, PALE[PURPLE], radius=0.20)
        text(ax, x, 1.36, label, 11, ha="center", weight="bold", color=PURPLE)
    arrow(ax, [(1.76, 1.54), (5.40, 1.54)], PURPLE)
    text(ax, 3.58, 1.94, r"$d\geq d_p$ 且 $F\geq F_p$，持续 $N_p$ 个样本", ha="center")
    text(ax, 3.58, 1.31, "触发一次已声明的按压状态转移", ha="center", color=PURPLE)
    arrow(ax, [(5.42, 1.07), (5.42, 0.89), (1.74, 0.89), (1.74, 1.08)], PURPLE)
    text(ax, 3.58, 0.68, r"$d\leq d_r$ 且 $F\leq F_r$，持续 $N_r$ 个样本", ha="center")
    return fig


def observation_acceptance():
    fig, ax = canvas(
        "固定观测与分层验收合同",
        "验收重新计算证据；仅凭渲染外观不能判定任务成功。",
    )
    panel(ax, 0.22, 2.76, 3.26, 1.94, "a", "标准观测")
    panel(ax, 3.68, 2.76, 3.30, 1.94, "b", "缺陷定位", AMBER)
    panel(ax, 0.22, 0.55, 3.26, 1.98, "c", "候选回归", TEAL)
    panel(ax, 3.68, 0.55, 3.30, 1.98, "d", "独立审计", PURPLE)
    camera(ax, 0.61, 3.76, 0.27)
    frame(ax, 1.02, 3.43, 0.87, 0.63, BLUE, roi=True)
    frame(ax, 2.12, 3.43, 0.87, 0.63, TEAL, roi=True)
    arrow(ax, [(0.82, 3.76), (0.98, 3.76)], BLUE)
    text(ax, 1.46, 3.19, "参考观测", ha="center")
    text(ax, 2.56, 3.19, "候选观测", ha="center")
    text(ax, 1.85, 2.91, "固定视角 / ROI / 输入哈希", ha="center", color=GRAY)
    # Defect evidence localizes a mismatch, without identifying its root cause.
    box(ax, 3.97, 3.30, 1.02, 0.80, BLUE, PALE[BLUE])
    for x in [4.09, 4.36, 4.63]:
        box(ax, x, 3.42, 0.22, 0.51, GRAY, "white", radius=0.01)
    ax.add_patch(
        Rectangle(
            (4.32, 3.36),
            0.43,
            0.65,
            facecolor="none",
            edgecolor=AMBER,
            lw=1.4,
            zorder=6,
        )
    )
    arrow(ax, [(4.76, 3.70), (5.15, 3.70)], AMBER)
    text(ax, 5.27, 4.04, "资产 + ROI")
    text(ax, 5.27, 3.70, "指标 / 阈值")
    text(ax, 5.27, 3.36, "严重程度")
    text(ax, 5.33, 2.94, "根因：尚未确认", ha="center", color=AMBER)
    # The protocol hash is invariant while required checks are compared.
    for x, title, shade in [(0.55, "修复前", PALE[GRAY]), (2.09, "修复后", PALE[TEAL])]:
        box(ax, x, 1.48, 0.89, 0.47, TEAL if x > 1 else LINE, shade)
        text(ax, x + 0.445, 1.715, title, ha="center")
    arrow(ax, [(1.50, 1.72), (2.02, 1.72)], TEAL)
    text(ax, 1.85, 2.08, "仅开发阶段", ha="center", color=TEAL)
    text(ax, 1.85, 1.22, "同一冻结协议", ha="center", color=GRAY)
    check(ax, 0.57, 0.93, TEAL)
    text(ax, 0.77, 0.93, "所有必选项均无退化")
    check(ax, 0.57, 0.68, TEAL)
    text(ax, 0.77, 0.68, "至少一项指标改善")
    # Structural acceptance and physical task acceptance consume different data.
    cube(ax, 3.99, 1.68, 0.26, PURPLE, fill=False)
    text(ax, 4.52, 1.97, "USD 结构", weight="bold", color=PURPLE)
    text(ax, 4.52, 1.70, "图层 · 包围盒 · 关节")
    line(ax, [(3.97, 1.43), (6.70, 1.43)], LINE, 0.8)
    line(
        ax,
        [(3.98, 1.02), (4.09, 1.02), (4.16, 1.27), (4.23, 1.02), (4.37, 1.02)],
        PURPLE,
        1.2,
    )
    text(ax, 4.52, 1.20, "实测任务", weight="bold", color=PURPLE)
    text(ax, 4.52, 0.93, "传感器与刚体轨迹回放")
    text(ax, 5.33, 0.67, "该测试任务的 20 项检查", ha="center", color=GRAY)
    return fig


def render_evidence():
    fig, ax = canvas(
        "原生渲染与证据绑定的交付",
        "巡览仅为预览；任务成功由实测记录的独立审计判定。",
    )
    panel(ax, 0.22, 2.74, 3.26, 1.96, "a", "场景巡览", BLUE)
    panel(ax, 3.68, 2.74, 3.30, 1.96, "b", "实测任务回放", TEAL)
    panel(
        ax,
        0.22,
        0.55,
        6.76,
        1.97,
        "c",
        "原生 RGB 采集、编码与凭据",
        PURPLE,
    )
    # Survey poses rotate eye-target delta around declared world Z.
    cube(ax, 1.41, 3.33, 0.43, BLUE)
    ax.add_patch(
        Arc((1.69, 3.57), 1.83, 0.96, theta1=20, theta2=165, edgecolor=BLUE, lw=1.1)
    )
    for theta in [35, 90, 145]:
        r = np.deg2rad(theta)
        x, y = 1.69 + 0.915 * np.cos(r), 3.57 + 0.48 * np.sin(r)
        camera(ax, x, y, 0.18, BLUE)
        line(ax, [(x, y - 0.10), (1.69, 3.62)], LINE, 0.75, dashed=True)
    text(ax, 1.85, 4.24, "包围盒机位或显式相机", ha="center", color=GRAY)
    text(ax, 1.85, 3.05, r"$\theta_i=\Theta\,i/\max(1,N-1)$", ha="center")
    text(ax, 1.85, 2.87, "不代表导航或任务成功", ha="center", color=GRAY)
    # Continuous sensor log and the stored 25 Hz body poses are separate streams.
    x = np.linspace(4.04, 6.65, 70)
    y = 3.60 + 0.22 * np.sin(np.linspace(-1.1, 1.7, 70))
    ax.plot(x, y, color=TEAL, lw=1.3)
    idx = np.arange(0, 70, 7)
    ax.scatter(x[idx], y[idx], s=13, color=TEAL, zorder=6)
    text(ax, 5.33, 4.25, "已记录的刚体与按钮帽位姿", ha="center", color=GRAY)
    line(ax, [(4.04, 3.27), (6.65, 3.27)], LINE)
    for xx in x[idx]:
        line(ax, [(xx, 3.22), (xx, 3.32)], TEAL, 0.8)
    text(ax, 5.33, 3.06, "25 fps · 直接回放实测位姿", ha="center")
    text(ax, 5.33, 2.87, "独立副本；禁用物理更新", ha="center", color=GRAY)
    # Distinct sources converge only at rendering, not at a success classifier.
    arrow(ax, [(1.85, 2.72), (1.85, 2.57)], BLUE)
    arrow(ax, [(5.33, 2.72), (5.33, 2.57)], TEAL)
    frame(ax, 0.48, 1.26, 1.08, 0.68, PURPLE)
    text(ax, 1.02, 0.98, "RTX RGB", ha="center", color=PURPLE)
    text(ax, 1.02, 0.73, "时间轴暂停", ha="center", color=GRAY)
    arrow(ax, [(1.68, 1.62), (2.04, 1.62)], PURPLE)
    for dx, dy in [(0.10, 0.09), (0, 0)]:
        frame(ax, 2.15 + dx, 1.32 + dy, 0.83, 0.52, BLUE)
    text(ax, 2.62, 0.98, "RGB 帧序列", ha="center")
    text(ax, 2.62, 0.73, "固定分辨率", ha="center", color=GRAY)
    arrow(ax, [(3.15, 1.62), (3.53, 1.62)], PURPLE)
    box(ax, 3.65, 1.30, 1.00, 0.60, PURPLE, PALE[PURPLE])
    ax.add_patch(
        Polygon(
            [(4.05, 1.43), (4.05, 1.76), (4.32, 1.60)],
            facecolor=PURPLE,
            edgecolor="none",
            zorder=4,
        )
    )
    text(ax, 4.15, 0.98, "编码为 MP4", ha="center")
    text(ax, 4.15, 0.73, "核验视频尺寸", ha="center", color=GRAY)
    arrow(ax, [(4.77, 1.62), (5.16, 1.62)], PURPLE)
    box(ax, 5.31, 1.24, 1.38, 0.71, TEAL, PALE[TEAL])
    text(ax, 6.00, 1.71, "SHA-256", ha="center", weight="bold", color=TEAL)
    text(ax, 6.00, 1.45, "绑定产物凭据", ha="center", color=TEAL)
    text(ax, 6.00, 0.98, "场景 · 位姿", ha="center")
    text(ax, 6.00, 0.73, "代码 · 视频", ha="center", color=GRAY)
    return fig


FIGURES = {
    "module_input_gaussians": input_gaussians,
    "module_registration_geometry": registration_geometry,
    "module_button_contact": button_contact,
    "module_observation_acceptance": observation_acceptance,
    "module_render_evidence": render_evidence,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/report/figures"))
    parser.add_argument("--only", choices=sorted(FIGURES))
    parser.add_argument(
        "--export-png",
        action="store_true",
        help="Also export PNGs to dist/report-figures",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    png_dir = Path(__file__).resolve().parents[1] / "dist/report-figures"
    if args.export_png:
        png_dir.mkdir(parents=True, exist_ok=True)
    selected = {args.only: FIGURES[args.only]} if args.only else FIGURES
    for name, builder in selected.items():
        fig = builder()
        for extension in ("svg", "pdf") + (("png",) if args.export_png else ()):
            metadata = {"Creator": "SimReady 原生方法图生成器"}
            if extension == "pdf":
                metadata.update(CreationDate=None, ModDate=None)
            elif extension == "svg":
                metadata["Date"] = None
            target = (
                png_dir if extension == "png" else args.out
            ) / f"{name}.{extension}"
            fig.savefig(target, dpi=300, metadata=metadata)
            if extension == "svg":
                target.write_text(
                    "\n".join(line.rstrip() for line in target.read_text().splitlines())
                    + "\n"
                )
        plt.close(fig)
        print(name)


if __name__ == "__main__":
    main()
