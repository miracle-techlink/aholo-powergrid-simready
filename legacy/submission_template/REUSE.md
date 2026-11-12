# 标准化提交模板 —— 下次比赛怎么复用

本目录(submission/)是"黑客松提交管线"的通用模板,Aholo 这场是首个实例。换比赛只改 3 处:

## 1. submission_config.yaml
- `meta.*`: 比赛名/base_url/my_page/deadline/activity_code
- `project.*`: 标题/一句话/摘要
- `required.*` / `bonus.*`: 按新比赛要求增删字段(auto_submit.fill 的字段映射同步加一行)
- `compliance.*`: 硬性要求(赞助商技术强制使用类)

## 2. scripts/common.py
- KILL_POPUP: 换站点的弹窗特征(Aholo 是 geo 弹窗,别的站可能是 cookie 墙/公告)
- CHROMIUM/UA: 通用
- 响应信封 `{"f","c","m","d"}` 解析: 若新站不是这套,在 NetLog 后加 normalizer

## 3. 流程四步(不变)
```
precheck.py          # 匿名窗口检查(有公开 config API 的比赛才适用;没有就删)
login_setup.py       # 一次人工登录 → storage_state.json
discover_endpoints.py# 一次人工走流程 → endpoints.json(之后全自动)
auto_submit.py       # dry-run → --submit
```

## 经验沉淀(Aholo 实测,2026-11-10)
- SPA 官网 WebFetch/curl 全是空壳,必须 headless chromium 渲染后取 innerText
- 弹窗按"布局特征"删(fixed+≥80%视口),按文本匹配会误删 SPA 根节点
- 提交类端点几乎必然在登录后才暴露 → discover 阶段人工走一遍+抓包是唯一可靠路径
- 文件上传优先 `set_input_files`(能触发前端校验),慢/大文件再换抓包拿到的直传 API
- 最后一步提交永远留人工确认(--submit + y/N),自动错提交不可撤销
- 检查代理假 200: 本机 HTTP 代理对 HEAD 返回 200 Connection established,验远端要看 Content-Length
