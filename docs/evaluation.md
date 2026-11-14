# 统一观测、缺陷与验收

`configs/evaluation/suite.v1.json` 的 schema 为 1，benchmark 版本为 `real2sim-bench/0.3`。这是项目内部的开发验收协议，不是已经发布的公共 benchmark，也没有商业交付认证。

## 资产一致性覆盖整个场景

按钮只是可交互零件的一种。通用资产清单以 **结构—物体—零件—关系** 表达：房间/墙/地面、柜体/货架/桌椅/管道、门板/抽屉/阀门/按钮/仪表，以及从属、邻接、连接等关系。对同一场景、同一观测范围、同一坐标系中的参考和候选，检查：

- 漏出/多出资产、类别和层级、身份与父对象；
- 位置、尺寸、朝向、材质类别；
- 状态与可操作能力标签；
- 资产间关系。

具体合同在 `configs/evaluation/asset_consistency.v1.json`。跨场景复用的是合同和规则；不会把不同房间的资产 ID 当作同一物体匹配。源清单不完整时，多出资产计数为未知；没有参考支持的功能/材质/物理参数不能由估计自动升级为真实。

当前通用资产模块比较**已提供的参考图与候选图**。自动源资产提取、可信渲染及匹配器仍待适配；可操作能力标签正确也不等于物理按压或任务成功。

## 可运行示例

安装 README 的 benchmark/test 依赖后：

```bash
python examples/scene_evaluation/prepare_fixture.py --out runs/benchmark-input
simready evaluation run \
  --protocol runs/benchmark-input/protocol.json \
  --candidate runs/benchmark-input/candidate.json \
  --out runs/benchmark-result
```

示例是相同的合成 RGB 图，预期 `PASS` 且 `release_authorized=false`。它只验证观测器接口，没有重建、物理或真实资产质量含义。输出目录必须不存在。返回码：0 表示此协议通过，2 表示失败/不完整，3 表示无效输入。

## 独立边界

候选和冻结协议进入独立进程，输出 scorecard、原始观测及开发集缺陷。自动修复与经验库在完整分支，main 仅执行独立验收。

检查采用必需项全部通过，不能让平均视觉指标抵消关键资产、几何、物理或任务失败。RGB 无自动缩放/曝光配准；保留固定 ROI 和最差区域指标。候选自报的 `passed`、物理成功或总分不能替代原始证据。协议、输入内容和验收代码有哈希绑定，开发集产生细粒度缺陷，密封模式不向修复 agent 输出缺陷清单。

现阶段尚缺：自动完整视角与真实覆盖测量、可信渲染/源资产提取、LPIPS/时序/物理/任务采集适配器、USD 全依赖闭包验证、跨场景密封数据集及阈值标定。新解释器进程仅提供状态分离，没有 OS 权限隔离；最终发布权必须放到另一服务。所有报告保留 `release_authorized=false`。
