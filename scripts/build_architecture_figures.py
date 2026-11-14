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
    """One portrait methods diagram: data, feedback and execution boundaries."""
    fig, ax = plt.subplots(figsize=(8.27, 11.69))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 8.27), ylim=(0, 11.69), aspect="equal")
    ax.axis("off")
    text(ax, 0.50, 11.35, "从视频到机器人 Demo：完整 Pipeline", 17, weight="bold")
    text(
        ax,
        0.50,
        11.02,
        "算法 · 阶段产物 · 控制与修复反馈 · 当前执行范围",
        11,
        color=MUTED,
    )

    def block(y, h, title, lines, color=BLUE, dashed=False):
        rect(ax, 0.62, y, 5.08, h, color=color, fill=PALE[color], dashed=dashed)
        text(ax, 0.80, y + h - 0.17, title, 11.8, color=color, weight="bold")
        for i, label in enumerate(lines):
            text(ax, 0.80, y + h - 0.39 - i * 0.20, label, 10.0)

    def down(top, bottom, label):
        arrow(ax, [(3.16, top), (3.16, bottom)], color=BLUE)
        text(ax, 3.30, (top + bottom) / 2, label, 8.8, color=MUTED)

    block(10.24, 0.56, "输入：真实场景视频与来源登记", [], BLUE)
    text(ax, 5.48, 10.44, "原片哈希", 9.3, ha="right", color=MUTED)
    for x in (1.82, 4.49):
        arrow(ax, [(3.16, 10.21), (3.16, 10.10), (x, 10.10), (x, 9.98)], color=BLUE)
    rect(ax, 0.62, 9.12, 2.40, 0.83, color=BLUE, fill=PALE[BLUE])
    text(ax, 0.80, 9.72, "① 参考观测", 11.6, color=BLUE, weight="bold")
    text(ax, 0.80, 9.44, "抽帧 · 拉普拉斯方差", 10.2)
    text(ax, 0.80, 9.23, "签名汉明距离 · 来源记录", 9.5)
    rect(ax, 3.30, 9.12, 2.40, 0.83, color=BLUE, fill=PALE[BLUE])
    text(ax, 3.48, 9.72, "② 三维重建", 11.6, color=BLUE, weight="bold")
    text(ax, 3.48, 9.44, "完整原片 → Aholo", 10.2)
    text(ax, 3.48, 9.23, "3DGS 外观 → PLY / SPZ", 9.5)
    arrow(ax, [(4.49, 9.09), (4.49, 8.98), (3.16, 8.98), (3.16, 8.86)], color=BLUE)
    text(
        ax,
        3.30,
        8.97,
        "中心／协方差／透明度／球谐",
        8.2,
        color=MUTED,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.5},
    )
    block(
        8.04,
        0.79,
        "③ 几何准备：坐标、尺度与地面",
        ["正向相似变换 · RANSAC＋SVD 平面估计", "输出：世界系高斯、地面与尺度来源"],
        BLUE,
    )
    down(8.01, 7.78, "统一坐标与长度单位")
    block(
        6.60,
        1.15,
        "④ 资产构建：外观／碰撞／交互三层",
        [
            "通用：保留高斯＋占据体素＋规格驱动对象",
            "离线：SAM 表面分组、纹理映射、独立几何补建",
            "输出：对象身份、局部坐标、USD 与依赖资产",
        ],
        TEAL,
    )
    # Reference images constrain surface/texture identity and later evaluation.
    arrow(
        ax,
        [(1.82, 9.09), (1.82, 8.96), (0.35, 8.96), (0.35, 7.15), (0.59, 7.15)],
        color=BLUE,
    )
    text(ax, 0.18, 7.99, "参考图像与区域", 9.0, color=BLUE, rotation=90, ha="center")
    down(6.57, 6.34, "可编辑、可组合的对象")
    block(
        5.57,
        0.74,
        "⑤ 物理建模与结构检查",
        [
            "PhysX 刚体／滑动关节／弹簧—阻尼；USD 审计",
            "输出：USD 场景；结构通过≠任务通过",
        ],
        TEAL,
    )
    down(5.54, 5.31, "另需任务配置与机器人标定")
    block(
        4.22,
        1.06,
        "⑥ 机器人执行：Lite3＋Z1 案例",
        [
            "已知停靠点反馈＋PPO 步态；HSV＋RGB-D 定位",
            "TCP／FK → 有界最小二乘 IK → 路径碰撞检查",
            "状态机＋关节驱动 ⇄ PhysX＋传感器反馈",
        ],
        TEAL,
    )
    down(4.19, 3.96, "实际轨迹／接触力／位移／事件")
    block(
        3.21,
        0.72,
        "⑦ 独立验收：分别判定，分别留证",
        [
            "固定视角 RGB／深度／实例；结构与任务各自验收",
            "变电站：接触、事件、FK 重算等 20 项任务审计",
        ],
        PURPLE,
    )
    down(3.18, 2.95, "符合任务合同的测量回放")
    block(
        2.23,
        0.69,
        "⑧ 原生渲染与交付",
        [
            "Isaac Sim RTX → 原生帧 → 编码与帧数检查",
            "交付：Demo 视频＋USD 资产＋测量与验收报告",
        ],
        TEAL,
    )

    rect(ax, 6.00, 9.12, 1.82, 1.68, color=MUTED, fill=PALE[MUTED])
    text(ax, 6.91, 10.54, "外部条件", 11.4, color=INK, weight="bold", ha="center")
    for y, label in [
        (10.18, "已知尺寸／世界轴"),
        (9.93, "相机内外参"),
        (9.68, "对象与物理参数"),
        (9.43, "机器人／策略／TCP"),
    ]:
        text(ax, 6.91, y, label, 9.3, ha="center", color=MUTED)
    arrow(
        ax,
        [(5.97, 9.45), (5.85, 9.45), (5.85, 8.66), (5.73, 8.66)],
        color=MUTED,
        dashed=True,
    )

    rect(ax, 6.00, 8.04, 1.82, 0.79, color=AMBER, fill=PALE[AMBER], dashed=True)
    text(ax, 6.91, 8.61, "重建研究支路", 10.3, color=AMBER, weight="bold", ha="center")
    text(ax, 6.91, 8.28, "VGGT → gsplat", 9.5, ha="center")
    arrow(ax, [(5.97, 8.36), (5.73, 8.36)], color=AMBER, dashed=True)

    rect(ax, 6.00, 6.60, 1.82, 1.18, color=AMBER, fill=PALE[AMBER], dashed=True)
    text(ax, 6.91, 7.53, "离线资产处理", 10.6, color=AMBER, weight="bold", ha="center")
    text(ax, 6.91, 7.25, "SIFT／MAGSAC", 9.5, ha="center")
    text(ax, 6.91, 7.01, "DINOv2 一致性", 9.5, ha="center")
    text(ax, 6.91, 6.78, "照片纹理候选", 9.5, ha="center")
    arrow(ax, [(5.97, 7.16), (5.73, 7.16)], color=AMBER, dashed=True)

    rect(ax, 6.00, 3.21, 1.82, 3.10, color=PURPLE, fill=PALE[PURPLE], dashed=True)
    text(ax, 6.91, 6.06, "质量修复回路", 10.8, color=PURPLE, weight="bold", ha="center")
    for y, label in [
        (5.72, "冻结参考与协议"),
        (5.41, "缺陷定位"),
        (5.10, "Agent 选择方案"),
        (4.79, "可信插件执行"),
        (4.48, "独立重算／复验"),
        (4.17, "改善且无退化"),
        (3.77, "经验库／参数配方"),
        (3.47, "另行配置后接入"),
    ]:
        text(ax, 6.91, y, label, 9.4, ha="center")
    arrow(ax, [(5.73, 3.58), (5.97, 3.58)], color=PURPLE, dashed=True)
    arrow(
        ax,
        [
            (7.85, 4.80),
            (8.03, 4.80),
            (8.03, 7.94),
            (5.86, 7.94),
            (5.86, 7.45),
            (5.73, 7.45),
        ],
        color=PURPLE,
        dashed=True,
    )
    text(
        ax,
        8.13,
        6.50,
        "修复候选返回相关资产阶段",
        8.4,
        color=PURPLE,
        rotation=90,
        ha="center",
    )

    rect(ax, 6.00, 1.03, 1.82, 1.91, color=TEAL, fill=PALE[TEAL])
    text(ax, 6.91, 2.68, "另一交付出口", 10.6, color=TEAL, weight="bold", ha="center")
    for y, label in [
        (2.36, "新场景结构通过"),
        (2.08, "→ 相机巡览"),
        (1.80, "→ 场景候选视频"),
        (1.45, "G2：编排展示"),
        (1.19, "均不等于物理任务"),
    ]:
        text(ax, 6.91, y, label, 9.1, ha="center")
    # The alternate delivery follows structural validation, without passing robot control.
    arrow(ax, [(5.73, 5.92), (5.86, 5.92), (5.86, 2.65), (5.97, 2.65)], color=TEAL)

    rect(ax, 0.62, 1.03, 5.08, 0.94, color=MUTED, fill="white")
    text(ax, 0.80, 1.72, "读图边界", 10.5, weight="bold")
    text(ax, 0.80, 1.45, "实线：数据依赖；虚线：需配置／核查的支线或反馈。", 9.3)
    text(ax, 0.80, 1.21, "单图展示完整方法，不代表任意视频已全自动完成全部阶段。", 9.1)
    text(
        ax,
        0.62,
        0.69,
        "新视频执行通用构建与结构审计；已有场景执行专用预检与任务审计。",
        9.6,
        color=MUTED,
    )
    text(
        ax,
        0.62,
        0.40,
        "文件哈希、参数来源、代码与资产版本贯穿阶段，验收结论绑定对应产物。",
        9.5,
        color=MUTED,
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
    parser.add_argument(
        "--export-png",
        action="store_true",
        help="Also export PNGs to dist/report-figures",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    png_dir = Path(__file__).resolve().parents[1] / "dist/report-figures"
    if args.export_png:
        png_dir.mkdir(parents=True, exist_ok=True)
    for name, builder in (
        ("architecture_overview", overview),
        ("repair_closed_loop", repair),
        ("robot_control_runtime", robot),
    ):
        fig = builder()
        for suffix in ("svg", "pdf") + (("png",) if args.export_png else ()):
            metadata = {
                "Creator": "SimReady Chinese portrait architecture figure builder"
            }
            if suffix == "pdf":
                metadata.update(CreationDate=None, ModDate=None)
            elif suffix == "svg":
                metadata["Date"] = None
            output = (png_dir if suffix == "png" else args.out) / f"{name}.{suffix}"
            fig.savefig(output, dpi=240, metadata=metadata)
            if suffix == "svg":
                output.write_text(
                    "\n".join(line.rstrip() for line in output.read_text().splitlines())
                    + "\n"
                )
        plt.close(fig)
        print(f"{name}: portrait; editable Chinese SVG / vector PDF")


if __name__ == "__main__":
    main()
