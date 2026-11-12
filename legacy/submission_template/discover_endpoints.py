#!/usr/bin/env python3
"""登录态下人工走一遍 报名→组队→提交选题→提交作品,全程抓包 → 产出 endpoints.json。
之后 auto_submit.py 才能走 API 快车道(或至少知道表单字段名)。
用法: DISPLAY=:0 python3 discover_endpoints.py   (有头,你操作,脚本抓包)
你每完成一个阶段按一次回车,脚本打阶段标记到日志里。"""

import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from legacy.submission_template.common import (
    CFG,
    NetLog,
    launch,
    goto,
    shot,
    ROOT,
    safe_url,
)

STAGES = ["报名", "组队", "提交选题", "作品提交(传报告/视频)", "完成,退出"]


def main():
    net = NetLog()
    with sync_playwright() as p:
        browser, ctx, page = launch(p, headless=False, storage_state=True)
        net.attach(page)
        goto(page, CFG["meta"]["my_page"])
        marks = []
        for st in STAGES:
            input(f"\n>>> 请在浏览器完成: 【{st}】 然后回到终端按回车抓阶段标记...")
            marks.append({"stage": st, "t": time.time(), "url": safe_url(page.url)})
            try:
                shot(
                    page,
                    ROOT
                    / CFG["automation"]["screenshots_dir"]
                    / f"stage_{len(marks)}.png",
                )
            except Exception:
                pass
        out = net.save("discover")
        # 阶段标记注入请求流,便于对齐
        (out / "stage_marks.json").write_text(
            json.dumps(marks, ensure_ascii=False, indent=1)
        )
        # 汇总候选提交端点
        cands = [
            r
            for r in net.reqs
            if any(
                k in r["url"].lower()
                for k in (
                    "submit",
                    "work",
                    "team",
                    "signup",
                    "enroll",
                    "upload",
                    "file",
                    "project",
                    "topic",
                    "proposal",
                )
            )
        ]
        (out / "endpoint_candidates.json").write_text(
            json.dumps(cands, ensure_ascii=False, indent=1)
        )
        print(f"\n抓包 {len(net.reqs)} 条 → {out}/")
        print(f"候选提交端点 {len(cands)} 条 → endpoint_candidates.json")
        # 沉淀到 auth/endpoints.json(人工确认字段含义后即可给 auto_submit 用)
        ep = ROOT / CFG["automation"]["endpoints"]
        ep.parent.mkdir(parents=True, exist_ok=True)
        if not ep.exists():
            ep.write_text(
                json.dumps(
                    {
                        "_note": "discover_endpoints 产出,人工核对 stage 对齐后填 method/params 说明",
                        "candidates": cands,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
        browser.close()


if __name__ == "__main__":
    main()
