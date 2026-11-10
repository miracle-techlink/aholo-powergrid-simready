# 提交清单(截止 11-19 19:00,以 precheck.py 实时倒计时为准)

## 必交
- [ ] 报告书 PDF(template/报告书_骨架.md → latex-cn-report 排版)——含过程截图+资产迭代对比
- [ ] 4 分钟 Demo 视频(含真实运行画面) —— video-edit skill 组装
- [ ] 报名+建队(1-3人,单人也要建队;提交选题后 3000 积分按当时队员平分)

## 加分
- [ ] 小红书 #Aholo黑客松 发布,结果公布前保持公开 → 回填 config link
- [ ] 源代码(repo 公开链接或 zip → package/source_code.zip)
- [ ] 魔搭社区发布 → 20h Notebook GPU 额度

## 硬性合规 ⚠️
- [ ] **Aholo Lux3D 必须用于核心 3D 流程**(评审 30% 也是群核技术栈)
      动作: aholo_client.py 用配电房柜体照片跑 img-to-3d → GLB 入 Isaac 场景
      前置: AHOLO_API_KEY(待用户)

## 自动化管线
- [ ] python3 scripts/precheck.py            # 匿名,随时跑,看窗口+倒计时
- [ ] DISPLAY=:0 python3 scripts/login_setup.py   # 一次,手机验证码,存 storage_state
- [ ] DISPLAY=:0 python3 scripts/discover_endpoints.py  # 一次,人工走全流程抓 endpoint
- [ ] python3 scripts/auto_submit.py         # dry-run 演练
- [ ] python3 scripts/auto_submit.py --submit # 终提交(最后人工确认)
- [ ] 提交回执截图存档 runs/submit/after_submit.png

## 时间线
- 11.19 19:00 截止;11.20-11.30 评审;12 月初公布
- 建议: 11.17 出报告初稿+视频粗剪;11.18 全链 dry-run;11.19 上午终提交(留缓冲)
