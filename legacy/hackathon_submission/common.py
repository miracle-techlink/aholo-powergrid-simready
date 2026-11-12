#!/usr/bin/env python3
"""Aholo 提交管线共享库 —— 浏览器启动/geo弹窗清除/网络抓包/storage_state。
所有参数已在 2026-11-10 侦察中实测(见 /tmp/aholo_recon/)。"""

import json, os, sys, time, yaml
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
CFG = yaml.safe_load((ROOT / "submission_config.yaml").read_text())

CHROMIUM = "/usr/bin/chromium-browser"
UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 非中国 IP 每次页面加载必弹"前往国际站?"全屏遮罩,吞掉一切点击。
# 按布局特征(fixed/absolute + ≥80%视口)删除,别按文本匹配(会误删 SPA 根节点)。
KILL_POPUP = """() => {
  const els = Array.from(document.querySelectorAll('body *')).filter(e => {
    const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return (s.position === 'fixed' || s.position === 'absolute') && r.width >= innerWidth*0.8 && r.height >= innerHeight*0.8;
  });
  for (const e of els) {
    let node = e;
    while (node.parentElement && node.parentElement !== document.body) {
      const ps = getComputedStyle(node.parentElement);
      if (ps.position === 'fixed' || ps.position === 'absolute') node = node.parentElement; else break;
    }
    if (node !== document.body) node.remove();
  }
  document.body.style.overflow = 'auto';
  document.documentElement.style.overflow = 'auto';
}"""


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
    )


def safe_post_data(req):
    try:
        pd = req.post_data
        return "<redacted request payload>" if pd else None
    except Exception:
        try:
            buf = req.post_data_buffer
            return f"<binary {len(buf)} bytes>" if buf else None
        except Exception:
            return "<unreadable>"


class NetLog:
    """记录所有 XHR/fetch,只留提交相关 host + 关键头。"""

    KEEP_HEADERS = ("content-type", "x-os-type", "x-activity-channel")

    def __init__(self):
        self.reqs, self.resps = [], []

    def attach(self, page):
        page.on(
            "request",
            lambda r: (
                self.reqs.append(
                    {
                        "t": time.time(),
                        "method": r.method,
                        "url": safe_url(r.url),
                        "resource_type": r.resource_type,
                        "headers": {
                            k: v
                            for k, v in r.headers.items()
                            if k.lower() in self.KEEP_HEADERS
                        },
                        "post_data": safe_post_data(r),
                    }
                )
                if r.resource_type in ("xhr", "fetch")
                else None
            ),
        )
        page.on(
            "response",
            lambda r: (
                self.resps.append(
                    {
                        "t": time.time(),
                        "status": r.status,
                        "url": safe_url(r.url),
                        "body": "<response body omitted>",
                    }
                )
                if r.request.resource_type in ("xhr", "fetch")
                else None
            ),
        )

    def save(self, tag):
        out = ROOT / CFG["automation"]["screenshots_dir"] / tag
        out.mkdir(parents=True, exist_ok=True)
        (out / "requests.json").write_text(
            json.dumps(self.reqs, ensure_ascii=False, indent=1)
        )
        (out / "responses.json").write_text(
            json.dumps(self.resps, ensure_ascii=False, indent=1)
        )
        return out


def launch(pw, headless=True, storage_state=None):
    browser = pw.chromium.launch(
        executable_path=CHROMIUM,
        headless=headless,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    ss = ROOT / CFG["automation"]["storage_state"]
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 1600},
        user_agent=UA,
        locale="zh-CN",
        storage_state=str(ss) if storage_state and ss.exists() else None,
    )
    page = ctx.new_page()
    return browser, ctx, page


def goto(page, url, kill_popup=True, **kw):
    page.goto(url, wait_until="networkidle", timeout=45000, **kw)
    time.sleep(2)
    if kill_popup:
        page.evaluate(KILL_POPUP)
    return page


def shot(page, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
