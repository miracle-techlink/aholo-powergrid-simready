#!/usr/bin/env python3
"""一次性登录,沉淀 storage_state.json(cookies+localStorage),之后所有自动化免登录。
需要桌面(有 DISPLAY)人工输手机验证码 —— 这是全管线唯一需要人的一步。
用法: DISPLAY=:0 python3 login_setup.py
流程: 打开 aholo3d.cn → 你点"登录"→手机号→验证码→登录成功 → 脚本检测到登录态自动保存。"""
import time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import CFG, KILL_POPUP, launch, goto, shot, ROOT

def logged_in(page) -> bool:
    txt = page.evaluate("() => document.body.innerText")
    return ("退出" in txt) or ("我的" in txt and "登录" not in txt[:200]) or ("报名" in txt and "尚未报名" not in txt)

def main():
    ss = ROOT / CFG["automation"]["storage_state"]
    with sync_playwright() as p:
        browser, ctx, page = launch(p, headless=False)   # 有头,人工交互
        goto(page, CFG["meta"]["base_url"] + "/")
        print(">>> 浏览器已开。请在窗口里完成: 登录(手机号+验证码) → 直到页面出现你的账号信息。")
        print(">>> 脚本每 5s 检测一次登录态,最长等 5 分钟...")
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            try: page.evaluate(KILL_POPUP)
            except Exception: pass
            try:
                if logged_in(page):
                    break
            except Exception:
                pass
        else:
            print("!! 5 分钟未检测到登录态(检测启发式可能不准)。若你已登录,仍强制保存? [y/N]")
            if input().strip().lower() != "y":
                sys.exit(1)
        # 再到 /my 让站点种齐 cookie
        goto(page, CFG["meta"]["my_page"])
        time.sleep(3)
        ctx.storage_state(path=str(ss))
        shot(page, ROOT / CFG["automation"]["screenshots_dir"] / "login_state.png")
        print(f"✅ 登录态已存 {ss} ({ss.stat().st_size}B)。后续 precheck/discover/auto_submit 免登录。")
        browser.close()

if __name__ == "__main__":
    main()
