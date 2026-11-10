#!/usr/bin/env python3
"""自动提交。两级策略:
  L1(表单驱动,登录态即可用): 打开 /hackathon/my → 按 6 步管线逐个找入口 →
     从 submission_config.yaml 填标题/简介/链接 → 文件控件 set_input_files 上传报告与视频 →
     停在最后一步等人工确认(--submit 才真点提交)。
  L2(API 直传,需 discover_endpoints 产出并人工确认过 endpoints.json): 跳过 UI,快且稳。
默认 --dry-run(填完截图不提交)。用法:
  python3 auto_submit.py            # dry-run 演练,产出 runs/dryrun/ 截图链
  python3 auto_submit.py --submit   # 真提交(最后一步人工过目后点确认)
"""
import argparse, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import CFG, NetLog, launch, goto, shot, ROOT

def fill(page, cfg):
    """尽最大努力按可见文案定位输入框填写。找不到不报错,记 missing 清单。"""
    missing = []
    fields = {
        "项目名称|作品名称|标题": cfg["project"]["title"],
        "项目简介|作品简介|描述|介绍": cfg["project"]["abstract"].strip(),
        "Demo|演示|视频链接": next((l for l in cfg["required"]["demo"]["fallback_links"]), ""),
        "小红书": cfg["bonus"]["xiaohongshu"]["link"],
        "源代码|代码链接|GitHub": cfg["bonus"]["source_code"]["repo"],
        "魔搭|ModelScope|模型链接": cfg["bonus"]["modelscope"]["link"],
    }
    inputs = page.locator("input:visible, textarea:visible")
    n = inputs.count()
    for i in range(n):
        el = inputs.nth(i)
        ph = (el.get_attribute("placeholder") or "") + (el.get_attribute("aria-label") or "")
        for pat, val in list(fields.items()):
            if val and any(k in ph for k in pat.split("|")):
                el.fill(val); fields.pop(pat); break
    missing = [k for k, v in fields.items() if v]  # 没填掉的(链接为空的本来就不填)
    return missing

def upload(page, cfg, dry):
    upped, no_input = [], []
    files = [("报告", cfg["required"]["report"]["pdf"]),
             ("Demo视频", cfg["required"]["demo"]["video"])]
    handles = page.locator('input[type="file"]')
    for i in range(handles.count()):
        name, path = (files[i] if i < len(files) else (None, None))
        if not path: break
        p = ROOT / path if not Path(path).is_absolute() else Path(path)
        if not p.exists():
            no_input.append(f"{name}: 文件不存在 {p}"); continue
        try:
            handles.nth(i).set_input_files(str(p)); upped.append(f"{name}←{p.name}")
        except Exception as e:
            no_input.append(f"{name}: {e}")
    return upped, no_input

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true", help="真提交(否则 dry-run)")
    args = ap.parse_args()
    cfg = CFG
    tag = "dryrun" if not args.submit else "submit"
    net = NetLog()
    with sync_playwright() as p:
        browser, ctx, page = launch(p, headless=True, storage_state=True)
        net.attach(page)
        goto(page, cfg["meta"]["my_page"])
        txt = page.evaluate("() => document.body.innerText")
        state = "已报名?" if "尚未报名" not in txt else "尚未报名!先跑 login_setup+手动报名"
        print(f"当前状态: {state}")
        # 走到作品提交步骤(具体入口文案以登录后真实 DOM 为准,这里按常见文案尝试)
        for label in ["作品提交", "提交作品", "04 作品提交"]:
            try:
                el = page.get_by_text(label, exact=False).first
                el.scroll_into_view_if_needed(timeout=2000); el.click(timeout=3000)
                time.sleep(2); page.evaluate("(()=>{})")
                break
            except Exception:
                continue
        missing = fill(page, cfg)
        upped, no_input = upload(page, cfg, args.submit)
        out = net.save(tag)
        shot(page, out / "before_confirm.png")
        print(f"填写缺失(需人工/链接未回填): {missing}")
        print(f"已挂上传: {upped}")
        print(f"上传问题: {no_input}")
        if args.submit:
            ans = input("确认提交? 截图已存 before_confirm.png,看一眼后 y/N: ")
            if ans.strip().lower() == "y":
                for label in ["提交", "确认提交", "确定"]:
                    try:
                        page.get_by_text(label, exact=True).first.click(timeout=3000); break
                    except Exception: continue
                time.sleep(5); shot(page, out / "after_submit.png")
                print("提交动作已执行,回执见 after_submit.png 与 responses.json")
            else:
                print("已放弃提交(dry 结果保留)")
        else:
            print(f"[dry-run] 未点任何提交按钮。截图/抓包 → {out}/")
        browser.close()

if __name__ == "__main__":
    main()
