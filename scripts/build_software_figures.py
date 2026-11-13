"""Draw Chinese software composition and actual entry call chains in portrait."""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from build_architecture_figures import (
    AMBER,
    BLUE,
    INK,
    MUTED,
    PALE,
    PURPLE,
    TEAL,
    arrow,
    canvas,
    node,
    rect,
    text,
)


def card(ax, x, y, w, h, title, body, color=BLUE):
    rect(ax, x, y, w, h, color=color, fill=PALE[color])
    text(ax, x + 0.15, y + h - 0.22, title, 11, color=color, weight="bold")
    text(ax, x + 0.15, y + h * 0.36, body, 9.6)


def composition():
    fig, ax = canvas(
        "软件构造：十个领域与状态归属",
        "方框表示责任划分；本图不将所有模块画成默认顺序执行",
    )
    node(
        ax,
        0.38,
        7.92,
        6.44,
        0.55,
        "薄入口  cli / pipeline_entry",
        "选择模式、转交参数；不持有模型和物理状态",
        size=11,
        detail_size=9.2,
    )
    arrow(ax, [(3.6, 7.90), (3.6, 7.65)], color=BLUE)
    card(
        ax,
        0.38,
        6.65,
        6.44,
        0.98,
        "01  workflows  ·  编排与候选推进",
        "视频 / 已有场景 / 修复 / 性能各自编排\n持有阶段回执、当前候选和预算；失败停止、保留证据",
    )
    text(ax, 0.38, 6.38, "处理与执行：由所选工作流组织", 10, weight="bold")
    card(
        ax,
        0.38,
        4.88,
        3.08,
        1.27,
        "02  ingestion  ·  输入与云任务",
        "抽帧 / 清晰度 / 去重\nAholo 客户端 / 凭据 / 下载\n状态：帧清单、任务 receipt",
        TEAL,
    )
    card(
        ax,
        3.62,
        4.88,
        3.20,
        1.27,
        "03  simulation  ·  仿真领域",
        "USD 构造 / 交互 / 控制 / 录像\n任务进程组装感知、规划与端口\n状态：物理后端及任务局部变量",
        TEAL,
    )
    card(
        ax,
        0.38,
        3.86,
        6.44,
        0.84,
        "04  execution  ·  环境、进程与任务图",
        "启动 / 监督 / 资源租约 / 内容缓存 / 计时；通用 DAG 并非入口必经层",
        TEAL,
    )
    text(
        ax,
        0.38,
        3.59,
        "质量与复用：可组合的开发闭环，新视频默认不调用修复循环",
        9.8,
        weight="bold",
    )
    card(
        ax,
        0.38,
        2.32,
        3.08,
        1.05,
        "05  evaluation  ·  独立判定",
        "协议 → 原始观测 → 评分 / 回归\n状态：冻结协议与带哈希证据",
        PURPLE,
    )
    card(
        ax,
        3.62,
        2.32,
        3.20,
        1.05,
        "06  repair  ·  可信修改模板",
        "目录规划 / 注册表 / 有界提案\n状态：插件版本、参数与任务模板",
        PURPLE,
    )
    card(
        ax,
        0.38,
        1.09,
        3.08,
        1.05,
        "07  agents  ·  受限决策",
        "规则 / 本地 CLI / API 适配器\n只选方案；记录决策与真实用量",
        AMBER,
    )
    card(
        ax,
        3.62,
        1.09,
        3.20,
        1.05,
        "08  experience  ·  证据复用",
        "Episode / Skill / 性能记忆\nSQLite 持久化；验证后晋升",
        AMBER,
    )
    rect(ax, 0.38, 0.20, 6.44, 0.70, color=MUTED, fill=PALE[MUTED])
    text(
        ax,
        0.54,
        0.67,
        "09  core：JSON / 哈希 / 原子写入 / 基本校验",
        10.1,
        weight="bold",
    )
    text(
        ax,
        0.54,
        0.39,
        "10  geometry：相机 / 刚体逆变换 / 四元数顺序；均不持有任务状态",
        9.6,
    )
    return fig


def calls():
    fig, ax = canvas(
        "实际调用：一条命令，两种流水线",
        "箭头表示调用顺序；每个仿真阶段仍是独立 Python 子进程",
    )
    node(
        ax, 0.38, 7.99, 6.44, 0.54, "simready.cli → workflows.pipeline_entry", size=11.5
    )
    arrow(ax, [(2.02, 7.98), (2.02, 7.66)], color=BLUE)
    arrow(ax, [(5.20, 7.98), (5.20, 7.66)], color=TEAL)
    text(ax, 0.38, 7.76, "--video", 9.4, color=BLUE, weight="bold")
    text(ax, 3.70, 7.76, "--scene", 9.4, color=TEAL, weight="bold")
    card(
        ax,
        0.38,
        6.19,
        3.10,
        1.43,
        "宿主  video_pipeline",
        "video_frames.extract_frames\nreconstruction_stage：复用或云任务\n固化 PLY / scene-spec / input.json\nVideoStages 保存可恢复回执",
        BLUE,
    )
    card(
        ax,
        3.70,
        6.19,
        3.12,
        1.43,
        "宿主  scene_pipeline",
        "可选 bundle 导出 / 身份核验\n写场景配置与执行计划\n已有资产不触发新云重建\nSSH 委派到已准备的远端 Docker",
        TEAL,
    )
    arrow(ax, [(2.02, 6.18), (2.02, 5.89)], color=BLUE)
    arrow(ax, [(5.20, 6.18), (5.20, 5.89)], color=TEAL)
    node(
        ax,
        0.38,
        5.31,
        6.44,
        0.55,
        "execution.isaac_launcher / isaac_container",
        "显式解释器或 Docker；输入挂载、日志、退出码与启动回执",
        color=MUTED,
        size=10.6,
        detail_size=9.0,
    )
    arrow(ax, [(2.02, 5.30), (2.02, 5.00)], color=BLUE)
    arrow(ax, [(5.20, 5.30), (5.20, 5.00)], color=TEAL)
    text(ax, 0.38, 5.10, "执行环境中的 worker", 9.2, color=MUTED)
    rect(ax, 0.38, 1.66, 3.10, 3.33, color=BLUE, fill="white")
    text(ax, 0.53, 4.77, "video_execution.execute", 11, color=BLUE, weight="bold")
    text(ax, 0.53, 4.44, "输入摘要复核 → 环境检查", 10)
    node(
        ax,
        0.57,
        3.63,
        2.72,
        0.57,
        "scene_preparation",
        "分层 USD / 资产目录 / 准备记录",
        size=10.4,
        detail_size=8.6,
    )
    arrow(ax, [(1.93, 3.61), (1.93, 3.36)], color=BLUE)
    node(
        ax,
        0.57,
        2.77,
        2.72,
        0.57,
        "evaluation.scene_package",
        "独立打开 USD；只做结构审计",
        size=10.0,
        detail_size=9,
    )
    arrow(ax, [(1.93, 2.75), (1.93, 2.50)], color=BLUE)
    node(
        ax,
        0.57,
        1.91,
        2.72,
        0.57,
        "scene_video",
        "巡览 → 帧 / 相机 / 编码凭据",
        size=10.4,
        detail_size=9,
    )
    rect(ax, 3.70, 1.66, 3.12, 3.33, color=TEAL, fill="white")
    text(
        ax, 3.85, 4.77, "scene_execution.execute_scene", 9.8, color=TEAL, weight="bold"
    )
    text(ax, 3.85, 4.44, "包 / 环境 → scene_delivery → 抽帧", 9.3)
    rect(ax, 3.89, 2.67, 2.74, 1.54, color=TEAL, fill=PALE[TEAL])
    text(ax, 4.03, 3.99, "变电站：每阶段成功才继续", 9.5, color=TEAL, weight="bold")
    text(ax, 5.26, 3.63, "substation_mission", 10.4, ha="center")
    arrow(ax, [(5.26, 3.46), (5.26, 3.31)], color=TEAL, scale=7)
    text(ax, 5.26, 3.15, "mission_audit → mission_video", 9.5, ha="center")
    text(ax, 5.26, 2.86, "实测轨迹          独立回放", 8.7, color=MUTED, ha="center")
    node(
        ax,
        3.89,
        1.91,
        2.74,
        0.57,
        "星巴克：cafe_video",
        "G2 编排；不产生物理导航成绩",
        color=TEAL,
        size=10.0,
        detail_size=8.8,
    )
    arrow(ax, [(2.02, 1.64), (2.02, 1.37)], color=BLUE)
    arrow(ax, [(5.20, 1.64), (5.20, 1.37)], color=TEAL)
    node(
        ax,
        0.38,
        0.70,
        6.44,
        0.64,
        "宿主再次核验产物身份与交付合同 → summary.json",
        "结构通过、任务通过、视频合格分别记账；文件存在不足以判成功",
        color=MUTED,
        size=10.5,
        detail_size=9.2,
    )
    text(
        ax,
        0.38,
        0.38,
        "prepare-only 跳过环境检查和巡览；修复循环通过另一工作流显式启动。",
        9.2,
        color=INK,
    )
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/report/figures"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, build in (
        ("software_composition", composition),
        ("software_call_chain", calls),
    ):
        fig = build()
        for extension in ("svg", "pdf", "png"):
            metadata = (
                {"Date": None}
                if extension == "svg"
                else {"CreationDate": None, "ModDate": None}
                if extension == "pdf"
                else None
            )
            path = args.out / f"{name}.{extension}"
            fig.savefig(path, dpi=240, metadata=metadata)
            if extension == "svg":
                path.write_text(
                    "\n".join(line.rstrip() for line in path.read_text().splitlines())
                    + "\n"
                )
        plt.close(fig)
        print(name)


if __name__ == "__main__":
    main()
