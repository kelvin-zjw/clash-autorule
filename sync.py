# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
import json
import os
import urllib.request

import yaml

SUB_URL = os.environ["SUB_URL"]
GIST_ID = os.environ["GIST_ID"]
GIST_TOKEN = os.environ["GIST_TOKEN"]

HTTPS_TEST = "https://www.gstatic.com/generate_204"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "clash-sync"})
    return urllib.request.urlopen(req).read().decode("utf-8")


def api(path, method="GET", data=None):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    return json.loads(urllib.request.urlopen(req).read())


sub = yaml.safe_load(get(SUB_URL))
custom = yaml.safe_load(open("custom-rules.txt", encoding="utf-8").read())

sub["rules"] = custom + sub.get("rules", [])

# 3. 健康检查统一走 HTTPS，避免 HTTP 被节点劫持导致测速误判
for g in sub.get("proxy-groups", []):
    if "url" in g:
        g["url"] = HTTPS_TEST

# 2. Ghelper 组行为：fallback 兜底
for g in sub.get("proxy-groups", []):
    if g.get("name") == "Ghelper":
        g["type"] = "fallback"
        g["url"] = HTTPS_TEST
        g["interval"] = 300
        g["include-all"] = True
        g["proxies"] = ["DIRECT", "🌐 全球智能", "AI专用"]

# 4. 强制关闭 IPv6（本机无 IPv6 出口，fake-ip 返回 AAAA 会 no route to host）
sub["ipv6"] = False
dns = sub.setdefault("dns", {})
dns["ipv6"] = False

new = yaml.safe_dump(
    sub, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096
)

old = api(f"/gists/{GIST_ID}")["files"].get("config.yaml", {}).get("content", "")

if old == new:
    print("skip: no change")
else:
    api(
        f"/gists/{GIST_ID}",
        method="PATCH",
        data=json.dumps({"files": {"config.yaml": {"content": new}}}).encode(),
    )
    print("gist updated")
