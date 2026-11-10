#!/usr/bin/env python3
"""匿名预检:提交窗口是否开放/倒计时/团队上限。不需要登录态,纯 API。
用法: python3 precheck.py"""
import json, time, urllib.request
from common import CFG

URL = f"{CFG['meta']['base_url']}/aholo/api/growth/activity/config?code={CFG['meta']['activity_code']}"
req = urllib.request.Request(URL, headers={"x-os-type": "web", "x-activity-channel": "direct"})
d = json.loads(urllib.request.urlopen(req, timeout=15).read())
assert d.get("c") == "0", f"信封非成功: {d.get('c')} {d.get('m')}"
data = d["d"]

sub = data.get("submission", {})
close = sub.get("closesAt"); open_ = sub.get("opensAt")
now = time.time() * 1000
print(f"submission: implemented={sub.get('implemented')} enabled={sub.get('enabled')} reasonCode={sub.get('reasonCode')}")
if close:
    left = (close - now) / 3600000
    print(f"窗口: {time.strftime('%m-%d %H:%M', time.localtime(open_/1000))} → {time.strftime('%m-%d %H:%M', time.localtime(close/1000))}  距截止 {left:.1f} 小时")
    if now > close:
        print("!! 已截止")
        raise SystemExit(2)
team = data.get("team", {})
print(f"team.maxMembers={team.get('maxMembers')}")
print("结论: " + ("可提交" if sub.get("reasonCode") == "OPEN" else f"不可提交({sub.get('reasonCode')})"))
