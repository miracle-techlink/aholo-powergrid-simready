"""Build Chinese portrait architecture figures as editable SVG, PDF and PNG.

Run: python scripts/build_architecture_figures.py [--out docs/report/figures]
All panels and connector routes are drawn natively; no GPU or image assets.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon
from matplotlib.path import Path as MplPath

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
WIDTH, HEIGHT = 7.2, 9.5
FONT = "Noto Sans CJK SC"
font_manager.findfont(
    font_manager.FontProperties(family=FONT), fallback_to_default=False
)
plt.rcParams.update(
    {
        "font.family": FONT,
        "font.size": 10.5,
        "svg.fonttype": "none",
        "svg.hashsalt": "simready-architecture-zh-portrait-v2",
        # Noto CJK uses CFF outlines. Type 3 embeds vectors without a TrueType
        # mismatch; Matplotlib also reads ps.fonttype for PDF glyph subsets.
        "pdf.fonttype": 3,
        "ps.fonttype": 3,
        "axes.unicode_minus": False,
        "savefig.facecolor": "white",
    }
)


def text(ax, x, y, label, size=10.5, *, color=INK, weight="normal", ha="left", **kw):
    return ax.text(
        x,
        y,
        label,
        fontsize=size,
        color=color,
        fontweight=weight,
        ha=ha,
        va="center",
        linespacing=1.35,
        zorder=6,
        **kw,
    )


def rect(ax, x, y, w, h, *, color=MUTED, fill="white", dashed=False, lw=0.9):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0,rounding_size=0.065",
        facecolor=fill,
        edgecolor=color,
        linewidth=lw,
        linestyle=(0, (4, 3)) if dashed else "solid",
        zorder=2,
    )
    ax.add_patch(patch)
    return patch


def arrow(ax, points, *, color=INK, dashed=False, lw=1.0, scale=9):
    path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1))
    ax.add_patch(
        FancyArrowPatch(
            path=path,
            arrowstyle="-|>",
            mutation_scale=scale,
            linewidth=lw,
            color=color,
            linestyle=(0, (4, 3)) if dashed else "solid",
            joinstyle="round",
            capstyle="round",
            zorder=4,
        )
    )


def line(ax, points, *, color=LINE, lw=1.0):
    ax.plot(*zip(*points, strict=True), color=color, lw=lw, zorder=4)


def node(ax, x, y, w, h, title, detail="", *, color=BLUE, size=11.5, detail_size=10):
    rect(ax, x, y, w, h, color=color, fill=PALE[color])
    text(
        ax,
        x + w / 2,
        y + h * (0.67 if detail else 0.5),
        title,
        size,
        color=color,
        weight="bold",
        ha="center",
    )
    if detail:
        text(ax, x + w / 2, y + h * 0.28, detail, detail_size, color=MUTED, ha="center")


def canvas(title, subtitle):
    fig, ax = plt.subplots(figsize=(WIDTH, HEIGHT))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, WIDTH), ylim=(0, HEIGHT), aspect="equal")
    ax.axis("off")
    text(ax, 0.38, 9.13, title, 16, weight="bold")
    text(ax, 0.38, 8.78, subtitle, 10.2, color=MUTED)
    return fig, ax


def overview():
    fig, ax = canvas(
        "视频到交互 Demo：方法与交付",
        "共同来源，不同交付范围；机器人任务与场景巡览分别验收",
    )
    node(
        ax,
        0.48,
        7.76,
        6.24,
        0.68,
        "视频观测：抽帧、清晰度与来源登记",
        "原视频＋参考帧＋文件哈希；同视频划分不构成独立测试",
        color=BLUE,
        size=12.2,
        detail_size=10.3,
    )
    arrow(ax, [(3.60, 7.73), (3.60, 7.48)], color=BLUE)
    text(ax, 3.77, 7.61, "原片输入", 9.4, color=MUTED)
    rect(ax, 0.48, 6.59, 6.24, 0.86, color=BLUE, fill=PALE[BLUE])
    text(
        ax,
        3.60,
        7.22,
        "三维重建：空间外观底稿",
        12.2,
        color=BLUE,
        weight="bold",
        ha="center",
    )
    text(
        ax,
        3.60,
        6.97,
        "Aholo 原视频重建 → PLY / SPZ；服务内部训练为黑盒",
        10.1,
        ha="center",
    )
    text(
        ax,
        3.60,
        6.74,
        "研究支路 VGGT → gsplat：历史指标未达门槛，不替换交付底稿",
        9.4,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(3.60, 6.56), (3.60, 6.40), (2.25, 6.40), (2.25, 6.25)], color=AMBER)
    arrow(ax, [(3.60, 6.40), (5.61, 6.40), (5.61, 6.25)], color=TEAL)

    rect(ax, 0.48, 4.70, 3.55, 1.52, color=AMBER, fill=PALE[AMBER], dashed=True)
    text(
        ax, 2.255, 5.99, "历史离线资产准备", 12, color=AMBER, weight="bold", ha="center"
    )
    text(ax, 2.255, 5.68, "坐标／尺度与跨帧来源对应", 10.8, ha="center")
    text(ax, 2.255, 5.39, "纹理与几何恢复；未观测面估计", 10.6, ha="center")
    text(
        ax, 2.255, 5.08, "输出：经核查的外观与资产实例", 10.3, color=MUTED, ha="center"
    )
    rect(ax, 4.50, 4.70, 2.22, 1.52, color=TEAL, fill=PALE[TEAL])
    text(ax, 5.61, 5.99, "新视频候选入口", 12, color=TEAL, weight="bold", ha="center")
    text(ax, 5.61, 5.68, "保留高斯；按声明变换", 10.1, ha="center")
    text(ax, 5.61, 5.39, "估计碰撞＋规格对象", 10.3, ha="center")
    text(ax, 5.61, 5.08, "无自动语义／纹理修复", 10, color=MUTED, ha="center")
    arrow(ax, [(2.255, 4.67), (2.255, 4.43)], color=AMBER)
    arrow(ax, [(5.61, 4.67), (5.61, 4.43)], color=TEAL)
    node(
        ax,
        0.48,
        3.65,
        3.55,
        0.75,
        "碰撞与交互合同",
        "支撑／限位／关节＋接触与状态绑定",
        color=AMBER,
        size=11.8,
        detail_size=10,
    )
    node(
        ax,
        4.50,
        3.65,
        2.22,
        0.75,
        "独立结构审计",
        "引用、边界与关节合法性",
        color=PURPLE,
        size=11.5,
        detail_size=9.8,
    )
    arrow(ax, [(2.255, 3.62), (2.255, 3.37)], color=TEAL)
    arrow(ax, [(5.61, 3.62), (5.61, 3.37)], color=TEAL)
    node(
        ax,
        0.48,
        2.56,
        3.55,
        0.78,
        "机器人感知、导航与操作",
        "RGB-D 定位；Lite3 步态＋Z1 关节驱动",
        color=TEAL,
        size=11.8,
        detail_size=9.6,
    )
    node(
        ax,
        4.50,
        2.56,
        2.22,
        0.78,
        "原生相机巡览",
        "场景候选，不运行机器人",
        color=TEAL,
        size=11.5,
        detail_size=9.8,
    )
    arrow(ax, [(2.255, 2.53), (2.255, 2.40), (1.28, 2.40), (1.28, 2.27)], color=PURPLE)
    node(
        ax,
        0.48,
        1.48,
        1.60,
        0.76,
        "独立任务验收",
        "传感器／事件／FK",
        color=PURPLE,
        size=10.8,
        detail_size=9.2,
    )
    node(
        ax,
        2.46,
        1.48,
        1.57,
        0.76,
        "原生回放 Demo",
        "实测轨迹 → 视频",
        color=TEAL,
        size=10.4,
        detail_size=9.4,
    )
    arrow(ax, [(2.11, 1.86), (2.43, 1.86)], color=TEAL)
    text(ax, 2.27, 2.06, "通过", 9.0, color=TEAL, ha="center")
    arrow(ax, [(5.61, 2.53), (5.61, 2.27)], color=TEAL)
    node(
        ax,
        4.50,
        1.48,
        2.22,
        0.76,
        "场景巡览就绪",
        "尺度／任务／保真未验收",
        color=TEAL,
        size=11.5,
        detail_size=9.8,
    )
    text(
        ax,
        3.60,
        1.17,
        "G2 为编排展示；不进入 Lite3＋Z1 的物理任务门槛。",
        10.0,
        color=MUTED,
        ha="center",
    )
    rect(ax, 0.48, 0.54, 6.24, 0.44, color=LINE, fill="#F7F9FA")
    text(
        ax,
        3.60,
        0.76,
        "来源证据贯穿：观测／估计／配置／版本／传感器／视频",
        10.4,
        color=MUTED,
        ha="center",
    )
    text(
        ax,
        3.60,
        0.23,
        "虚线为离线准备；修复 Agent 是可组合框架，未默认贯穿新视频路径。",
        9.3,
        color=MUTED,
        ha="center",
    )
    return fig


def repair():
    fig, ax = canvas(
        "证据约束的修复闭环",
        "固定开发协议；Agent 选择已注册动作，独立验收决定是否更新",
    )
    rect(ax, 0.40, 7.44, 6.32, 1.12, color=LINE)
    text(ax, 0.58, 8.34, "（a）观测与缺陷定位", 11.8, color=BLUE, weight="bold")
    for x, title, detail in (
        (0.58, "当前候选 C_k", "资产＋证据"),
        (2.75, "标准观测", "固定视角与来源"),
        (4.92, "缺陷清单 D_k", "实例／区域／度量"),
    ):
        node(
            ax,
            x,
            7.59,
            1.60,
            0.54,
            title,
            detail,
            color=BLUE,
            size=10.7,
            detail_size=9.3,
        )
    arrow(ax, [(2.21, 7.86), (2.72, 7.86)], color=BLUE)
    arrow(ax, [(4.38, 7.86), (4.89, 7.86)], color=BLUE)
    arrow(ax, [(5.72, 7.41), (5.72, 7.25), (3.60, 7.25), (3.60, 7.10)], color=BLUE)
    rect(ax, 1.00, 6.20, 5.20, 0.87, color=BLUE, fill=PALE[BLUE])
    text(
        ax,
        3.60,
        6.82,
        "（b）Agent 选择修复提案",
        12,
        color=BLUE,
        weight="bold",
        ha="center",
    )
    text(ax, 3.60, 6.56, "规则／Codex／API 只返回提案 ID 与理由", 10.4, ha="center")
    text(
        ax,
        3.60,
        6.34,
        "注册实现、版本、参数范围与预算由宿主核对",
        10,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(3.60, 6.17), (3.60, 6.03), (2.10, 6.03), (2.10, 5.86)], color=TEAL)
    node(
        ax,
        0.75,
        5.18,
        2.70,
        0.65,
        "可信插件执行",
        "任务 DAG／独立输出目录",
        color=TEAL,
        size=11.5,
        detail_size=10,
    )
    node(
        ax,
        4.00,
        5.18,
        2.40,
        0.65,
        "新候选 C′",
        "资产产物＋不可变哈希",
        color=TEAL,
        size=11.5,
        detail_size=10,
    )
    arrow(ax, [(3.48, 5.505), (3.97, 5.505)], color=TEAL)
    arrow(ax, [(5.20, 5.15), (5.20, 4.98), (3.60, 4.98), (3.60, 4.86)], color=PURPLE)
    node(
        ax,
        1.00,
        4.08,
        5.20,
        0.75,
        "（c）独立验收器重算",
        "新进程读取冻结协议与观测 → 评分卡＋缺陷",
        color=PURPLE,
        size=12,
        detail_size=10.4,
    )
    arrow(ax, [(3.60, 4.05), (3.60, 3.85)], color=PURPLE)
    ax.add_patch(
        Polygon(
            [(3.60, 3.82), (5.00, 3.42), (3.60, 3.02), (2.20, 3.42)],
            closed=True,
            facecolor=PALE[PURPLE],
            edgecolor=PURPLE,
            lw=1,
            zorder=2,
        )
    )
    text(
        ax,
        3.60,
        3.42,
        "必选项至少一项改善\n且无必选项退化？",
        10.5,
        color=PURPLE,
        weight="bold",
        ha="center",
    )
    arrow(ax, [(2.17, 3.42), (1.50, 3.42), (1.50, 2.86)], color=TEAL)
    arrow(ax, [(5.03, 3.42), (5.70, 3.42), (5.70, 2.86)], color=MUTED)
    text(ax, 1.68, 3.10, "是", 10.5, color=TEAL)
    text(ax, 5.87, 3.10, "否", 10.5, color=MUTED)
    node(ax, 0.50, 2.30, 2.00, 0.53, "接受改进", color=TEAL, size=11.5)
    node(ax, 4.70, 2.30, 2.00, 0.53, "保留原候选", color=MUTED, size=11.5)
    arrow(ax, [(0.47, 2.565), (0.22, 2.565), (0.22, 7.86), (0.55, 7.86)], color=TEAL)
    arrow(ax, [(1.50, 2.27), (1.50, 2.15), (3.60, 2.15), (3.60, 2.02)], color=AMBER)
    line(ax, [(5.70, 2.27), (5.70, 2.15), (3.60, 2.15)], color=AMBER)
    node(
        ax,
        1.00,
        1.49,
        5.20,
        0.50,
        "成功与失败均记录 Episode／Memory",
        color=AMBER,
        size=11,
    )
    arrow(ax, [(3.60, 1.46), (3.60, 1.31)], color=AMBER)
    rect(ax, 0.65, 0.40, 5.90, 0.88, color=AMBER, fill=PALE[AMBER])
    text(
        ax,
        3.60,
        1.06,
        "Skill 晋升：已接受且整体通过",
        11.5,
        color=AMBER,
        weight="bold",
        ha="center",
    )
    text(
        ax,
        3.60,
        0.79,
        "至少 2 个不同场景（含验证）→ 协议／插件版本匹配",
        9.8,
        ha="center",
    )
    text(
        ax,
        3.60,
        0.54,
        "默认不自动晋升；退化退役；不更新基础模型权重",
        9.8,
        color=MUTED,
        ha="center",
    )
    arrow(
        ax,
        [(6.58, 0.84), (6.98, 0.84), (6.98, 6.64), (6.23, 6.64)],
        color=AMBER,
        dashed=True,
    )
    text(ax, 6.62, 6.85, "兼容经验", 9.3, color=AMBER, ha="center")
    text(
        ax,
        3.60,
        0.16,
        "缺证据、无进展或超预算即停止；开发接受不等于发布授权。",
        9.3,
        color=MUTED,
        ha="center",
    )
    return fig


def robot():
    fig, ax = canvas(
        "机器人控制与独立测量证据",
        "Lite3＋Z1 既有任务；高层感知、导航与操作仍受场景合同约束",
    )
    rect(ax, 0.48, 7.62, 6.24, 1.00, color=BLUE, fill=PALE[BLUE])
    text(ax, 0.67, 8.39, "任务监督器：按实测条件推进", 11.5, color=BLUE, weight="bold")
    states = [
        (1.22, 8.10, "站立"),
        (2.80, 8.10, "接近"),
        (4.38, 8.10, "停稳"),
        (5.96, 8.10, "伸臂"),
        (5.96, 7.80, "按压"),
        (4.38, 7.80, "撤离"),
        (2.80, 7.80, "收臂"),
        (1.22, 7.80, "成功"),
    ]
    for x, y, label in states:
        text(ax, x, y, label, 10.7, color=BLUE, ha="center")
    for i in (0, 1, 2):
        x, y, _ = states[i]
        arrow(ax, [(x + 0.28, y), (states[i + 1][0] - 0.28, y)], color=BLUE, scale=7)
    arrow(
        ax,
        [(6.24, 8.10), (6.45, 8.10), (6.45, 7.80), (6.24, 7.80)],
        color=BLUE,
        scale=7,
    )
    for i in (4, 5, 6):
        x, y, _ = states[i]
        arrow(ax, [(x - 0.28, y), (states[i + 1][0] + 0.28, y)], color=BLUE, scale=7)
    arrow(ax, [(3.60, 7.59), (3.60, 7.35)], color=BLUE)
    rect(ax, 0.48, 6.00, 6.24, 1.32, color=LINE)
    text(ax, 0.67, 7.09, "（a）任务控制器", 11.8, color=BLUE, weight="bold")
    node(
        ax,
        0.64,
        6.18,
        1.73,
        0.66,
        "已知地图导航",
        "位姿 → ONNX 步态",
        color=BLUE,
        size=10.6,
        detail_size=9.1,
    )
    node(
        ax,
        2.73,
        6.18,
        1.67,
        0.66,
        "RGB-D 定位",
        "唯一目标 → 表面点",
        color=BLUE,
        size=10.6,
        detail_size=9.0,
    )
    node(
        ax,
        4.87,
        6.18,
        1.69,
        0.66,
        "FK／IK 与避碰",
        "限位内规划与按压",
        color=BLUE,
        size=10.3,
        detail_size=9.0,
    )
    arrow(ax, [(4.43, 6.51), (4.84, 6.51)], color=BLUE, scale=8)
    line(ax, [(1.505, 6.15), (1.505, 5.89), (5.715, 5.89), (5.715, 6.15)], color=BLUE)
    arrow(ax, [(3.60, 5.89), (3.60, 5.73)], color=TEAL)
    rect(ax, 0.74, 4.70, 5.72, 1.00, color=TEAL, fill=PALE[TEAL])
    text(
        ax,
        3.60,
        5.47,
        "（b）设备端口 RobotInterface",
        12.2,
        color=TEAL,
        weight="bold",
        ha="center",
    )
    text(
        ax,
        3.60,
        5.17,
        "描述／观测／命令；关节顺序、单位与张量形状对齐",
        10.3,
        ha="center",
    )
    text(
        ax,
        3.60,
        4.91,
        "物理模式仅 joint_drive；任务中不写根位姿",
        10.3,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(3.60, 4.67), (3.60, 4.47)], color=TEAL)
    rect(ax, 0.74, 3.16, 5.72, 1.28, color=TEAL, fill=PALE[TEAL])
    text(
        ax,
        3.60,
        4.20,
        "组合机构：12 腿关节＋6 臂关节＋1 被动工具",
        11,
        color=TEAL,
        weight="bold",
        ha="center",
    )
    rect(ax, 1.69, 3.67, 1.12, 0.23, color=TEAL)
    for x, shift in ((1.82, -0.13), (2.04, 0.05), (2.47, -0.03), (2.69, 0.12)):
        line(
            ax,
            [(x, 3.67), (x + shift, 3.57), (x + shift - 0.08, 3.44)],
            color=TEAL,
            lw=1.2,
        )
        ax.add_patch(
            Circle(
                (x + shift, 3.57),
                0.025,
                facecolor="white",
                edgecolor=TEAL,
                lw=0.7,
                zorder=5,
            )
        )
    arm = [(2.48, 3.90), (2.98, 3.97), (3.51, 3.60), (4.08, 3.94), (4.61, 3.77)]
    line(ax, arm, color=TEAL, lw=1.5)
    for x, y in arm[:-1]:
        ax.add_patch(
            Circle((x, y), 0.045, facecolor="white", edgecolor=TEAL, lw=1, zorder=5)
        )
    spring = [(4.63, 3.77)]
    for i in range(8):
        spring.append((4.63 + 0.60 * (i + 0.5) / 8, 3.77 + (0.09 if i % 2 else -0.09)))
    spring.append((5.23, 3.77))
    line(ax, spring, color=AMBER, lw=1.1)
    line(ax, [(5.25, 3.62), (5.25, 3.92)], color=AMBER, lw=1.2)
    text(
        ax,
        3.60,
        3.27,
        "Lite3 基座                 Z1 机械臂            弹簧末端",
        10.3,
        color=MUTED,
        ha="center",
    )
    arrow(ax, [(3.60, 3.13), (3.60, 2.89)], color=TEAL)
    node(
        ax,
        0.74,
        2.22,
        5.72,
        0.64,
        "（c）PhysX：机器人与场景物理",
        "返回实测接触、位移、姿态与关节状态",
        color=TEAL,
        size=11.7,
        detail_size=10.1,
    )
    arrow(ax, [(6.49, 2.54), (6.98, 2.54), (6.98, 8.39), (6.75, 8.39)], color=TEAL)
    text(ax, 5.69, 8.39, "测量反馈", 10.1, color=TEAL, ha="center")
    arrow(ax, [(3.60, 2.19), (3.60, 1.97)], color=PURPLE)
    node(
        ax,
        0.74,
        1.43,
        5.72,
        0.51,
        "冻结日志：传感器／RGB-D／刚体轨迹",
        color=PURPLE,
        size=11,
    )
    arrow(ax, [(3.60, 1.40), (3.60, 1.27), (1.96, 1.27), (1.96, 1.15)], color=PURPLE)
    node(ax, 0.74, 0.62, 2.44, 0.50, "独立任务审计：20 项", color=PURPLE, size=10.7)
    node(ax, 4.02, 0.62, 2.44, 0.50, "原生 RTX 测量回放", color=TEAL, size=10.7)
    arrow(ax, [(3.21, 0.87), (3.99, 0.87)], color=TEAL)
    text(ax, 3.60, 1.07, "通过后", 9.3, color=TEAL, ha="center")
    text(
        ax,
        3.60,
        0.28,
        "任务审计仅覆盖当前合同；G2 是独立的 root_pose 编排分支。",
        9.6,
        color=MUTED,
        ha="center",
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
            metadata = {
                "Creator": "SimReady Chinese portrait architecture figure builder"
            }
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
        print(
            f"{name}: 7.2 x 9.5 in; editable Chinese SVG / vector PDF; PNG 1728 x 2280"
        )


if __name__ == "__main__":
    main()
