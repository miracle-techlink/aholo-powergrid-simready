"""Draw the asset-to-USD-to-Isaac method as an editable Chinese portrait figure."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

BLUE, TEAL, PURPLE, GRAY = "#3A6F9D", "#2A8078", "#786295", "#607080"
PALE = {BLUE: "#EEF4F9", TEAL: "#EDF6F3", PURPLE: "#F3F0F7", GRAY: "#F4F6F8"}


def build():
    plt.rcParams.update(
        {
            "font.family": "Noto Sans CJK SC",
            "svg.fonttype": "none",
            "svg.hashsalt": "simready-asset-method-zh",
            "pdf.fonttype": 3,
            "ps.fonttype": 3,
            "axes.unicode_minus": False,
        }
    )
    fig, ax = plt.subplots(figsize=(8.27, 11.69))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 8.27), ylim=(0, 11.69), aspect="equal")
    ax.axis("off")

    def label(x, y, value, size=10, color="#263442", bold=False, ha="left"):
        return ax.text(
            x,
            y,
            value,
            fontsize=size,
            color=color,
            va="center",
            ha=ha,
            fontweight="bold" if bold else "normal",
            linespacing=1.45,
        )

    def box(x, y, w, h, title, lines, color=BLUE, dashed=False):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0,rounding_size=0.055",
                facecolor=PALE[color],
                edgecolor=color,
                linewidth=1,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )
        label(x + 0.16, y + h - 0.20, title, 11, color, True)
        for i, value in enumerate(lines):
            label(x + 0.16, y + h - 0.48 - i * 0.23, value, 9.5)

    def arrow(x1, y1, x2, y2, color=BLUE, dashed=False):
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=10,
                linewidth=1,
                color=color,
                linestyle=(0, (4, 3)) if dashed else "solid",
            )
        )

    label(0.45, 11.30, "3D 资产如何成为 SimReady 场景", 17, bold=True)
    label(
        0.45, 10.97, "分离外观、接触和运动；组合为 USD，再由 Isaac Sim 执行", 10.6, GRAY
    )
    box(0.50, 9.96, 3.49, 0.74, "新视频输入", ["3DGS PLY＋显式场景规格"], BLUE)
    box(
        4.25,
        9.96,
        3.49,
        0.74,
        "已有场景输入",
        ["经准备的 USD、网格、纹理与机器人"],
        GRAY,
        True,
    )
    arrow(2.25, 9.94, 2.25, 9.71)
    arrow(6.00, 9.94, 6.00, 9.71, GRAY, True)
    box(
        0.50,
        8.79,
        7.24,
        0.90,
        "01  坐标与单位：先确定几何，再配置物理",
        [
            "高斯与派生碰撞使用源到世界变换；显式对象坐标已在世界系",
            "Z-up／每单位一米；真实尺度另需证据，姿态与关节轴显式声明",
        ],
        BLUE,
    )
    arrow(4.12, 8.77, 4.12, 8.51)
    label(4.29, 8.64, "同一世界系中的三种表示", 9.3, GRAY)
    for x, title, lines in [
        (
            0.50,
            "02a  外观层",
            ["高斯／网格／照片纹理", "颜色、粗糙度等视觉材质", "不把高斯当实体碰撞"],
        ),
        (
            2.98,
            "02b  碰撞层",
            ["体素盒／圆柱／有限地面", "几何形状＋CollisionAPI", "不可见也参与接触"],
        ),
        (
            5.46,
            "02c  交互对象",
            ["底座、帽体与局部坐标", "刚体角色＋关节连接", "稳定对象 ID／参数来源"],
        ),
    ]:
        box(x, 7.18, 2.28, 1.30, title, lines, TEAL)
        arrow(x + 1.14, 7.16, x + 1.14, 6.91, TEAL)
    box(
        0.50,
        5.62,
        7.24,
        1.26,
        "03  物理参数与运动约束",
        [
            "静态柜体：碰撞；底座：运动学刚体；按钮帽：动态刚体＋质量",
            "接触材质：静摩擦 0.8／动摩擦 0.7／恢复系数 0；均为配置预设",
            "滑动关节：轴与行程；力驱动：刚度、阻尼、最大力；工具接触保留",
            "质量显式写入；质心与惯量未独立标定；视觉材质不自动决定摩擦",
        ],
        TEAL,
    )
    arrow(4.12, 5.60, 4.12, 5.35, TEAL)
    label(4.29, 5.47, "保存结构、关系与参数", 9.3, GRAY)
    box(
        0.50,
        3.90,
        7.24,
        1.42,
        "04  OpenUSD 组合：scene.usda 中的 /World",
        [
            "/Appearance  ← appearance.usdc     外观与源变换",
            "/Collision      ← collision.usda          支撑与静态代理",
            "/Objects        ← objects.usda            盒体／按钮／关节／材料",
            "/Physics        → 重力 −Z、9.81 m/s²；相对引用＋资产目录＋来源摘要",
        ],
        BLUE,
    )
    for x in (2.25, 6.00):
        arrow(x, 3.88, x, 3.62, BLUE)
    box(
        0.50,
        2.44,
        3.49,
        1.15,
        "05a  新视频：加载与巡览",
        [
            "SimulationApp → USD 子层",
            "相机＋Replicator → 原生视频",
            "验证能加载、能渲染",
        ],
        TEAL,
    )
    box(
        4.25,
        2.44,
        3.49,
        1.15,
        "05b  已有任务：物理执行",
        [
            "Isaac Lab → 环境＋机器人",
            "关节驱动＋PhysX＋接触传感器",
            "验证支撑、操作、释放与回撤",
        ],
        TEAL,
    )
    for x in (2.25, 6.00):
        arrow(x, 2.42, x, 2.16, PURPLE)
    box(
        0.50,
        1.02,
        7.24,
        1.11,
        "06  分层验收与交付",
        [
            "结构：引用／边界／真实刚体和关节；物理：支撑／接触／行程／稳定性",
            "任务：图像定位、工具接触、事件和回撤；已有变电站按 20 项审计",
            "交付：分层 USD＋参数与来源＋视频／报告；各结论绑定实际验证范围",
        ],
        PURPLE,
    )
    label(
        0.50,
        0.68,
        "USD 是数据载体，SimReady 是面向用途的准备与验证；生成文件不等于任务成功。",
        10,
        GRAY,
    )
    label(
        0.50,
        0.39,
        "当前通用入口：结构审计＋巡览；未自动辨识真实惯量、材料、电气语义或机器人任务。",
        9.5,
        GRAY,
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
    fig = build()
    for ext in ("pdf", "svg", "png"):
        metadata = {"Creator": "SimReady asset method figure"}
        if ext == "pdf":
            metadata.update(CreationDate=None, ModDate=None)
        if ext == "svg":
            metadata["Date"] = None
        path = args.out / f"asset_simready_pipeline.{ext}"
        fig.savefig(path, dpi=240, metadata=metadata)
        if ext == "svg":
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text().splitlines())
                + "\n"
            )
    plt.close(fig)
    print(args.out / "asset_simready_pipeline.pdf")


if __name__ == "__main__":
    main()
