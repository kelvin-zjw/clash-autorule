# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
import json
import os
import re
import sys
import urllib.request

import yaml

# 是否注入 TUN 配置（mihomo 原生客户端可直接接管流量；Clash Verge 会用自己的开关覆盖，无副作用）
ENABLE_TUN = os.environ.get("ENABLE_TUN", "1") == "1"

HTTPS_TEST = "https://www.gstatic.com/generate_204"

AI_GROUP = "AI专用"

# 出口被 Gemini API 判为「不支持的地区」的节点（实测返回 400 User location is not supported
# for the API use）。这些节点跑普通上网没问题，所以保留在 proxies 里，但必须从 AI专用 组剔除：
# 否则 url-test 会因为它们延迟低而选中，所有 AI 请求都会被 Google 拒掉。
AI_BLOCKED_NODES = (
    "🇸🇬 新加坡",  # 出口 154.39.62.242 / 2602:f7f0::/32（AS18186 Nebula Global）
    "美国硅谷3",  # 出口 198.2.206.82（AS54600 PEG TECH）
    "美国硅谷6",  # 出口 142.0.141.137（AS54600 PEG TECH）
)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "clash-sync"})
    return urllib.request.urlopen(req).read().decode("utf-8")


def api(path, token, method="GET", data=None):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    return json.loads(urllib.request.urlopen(req).read())


def drop_ai_blocked_nodes(sub):
    """把 AI_BLOCKED_NODES 从 AI专用 组剔除，返回 (已排除, 没找到的)。"""
    for group in sub.get("proxy-groups", []):
        if group.get("name") != AI_GROUP:
            continue
        # include-all 的组要靠 exclude-filter 才能挡掉节点
        if group.get("include-all"):
            pattern = "|".join(re.escape(n) for n in AI_BLOCKED_NODES)
            existing = group.get("exclude-filter")
            group["exclude-filter"] = f"(?:{existing})|(?:{pattern})" if existing else pattern
            return list(AI_BLOCKED_NODES), []
        members = group.get("proxies") or []
        removed = [n for n in AI_BLOCKED_NODES if n in members]
        group["proxies"] = [n for n in members if n not in AI_BLOCKED_NODES]
        return removed, [n for n in AI_BLOCKED_NODES if n not in removed]
    raise RuntimeError(f"proxy-groups 里找不到「{AI_GROUP}」，无法排除被封节点")


def build(sub, custom):
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

    # 6. AI专用 组剔除被 Google 判为不支持地区的节点
    removed, missing = drop_ai_blocked_nodes(sub)
    print(f"{AI_GROUP}: 排除 {removed or '无'}")
    if missing:
        print(
            f"警告：{AI_GROUP} 组里没有 {missing}，订阅可能已改名，请更新 AI_BLOCKED_NODES",
            file=sys.stderr,
        )

    # 4. 强制关闭 IPv6（本机无 IPv6 出口，fake-ip 返回 AAAA 会 no route to host）
    sub["ipv6"] = False
    dns = sub.setdefault("dns", {})
    dns["ipv6"] = False

    # 5. TUN 接管（能做多少做多少）
    if ENABLE_TUN:
        tun = sub.setdefault("tun", {})
        tun.setdefault("enable", True)
        tun.setdefault("stack", "gvisor")
        tun.setdefault("auto-route", True)
        tun.setdefault("auto-detect-interface", True)
        tun.setdefault("strict-route", False)
        # TUN 下需要劫持 DNS 到内核，才能让 fake-ip 域名规则生效
        dns.setdefault("enable", True)
        dns.setdefault("enhanced-mode", "fake-ip")
        if not tun.get("dns-hijack"):
            tun["dns-hijack"] = ["any:53"]

    return sub


def main():
    sub_url = os.environ["SUB_URL"]
    gist_id = os.environ["GIST_ID"]
    gist_token = os.environ["GIST_TOKEN"]

    sub = yaml.safe_load(get(sub_url))
    custom = yaml.safe_load(open("custom-rules.txt", encoding="utf-8").read())

    new = yaml.safe_dump(
        build(sub, custom),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )

    old = api(f"/gists/{gist_id}", gist_token)["files"].get("config.yaml", {}).get("content", "")

    if old == new:
        print("skip: no change")
    else:
        api(
            f"/gists/{gist_id}",
            gist_token,
            method="PATCH",
            data=json.dumps({"files": {"config.yaml": {"content": new}}}).encode(),
        )
        print("gist updated")


if __name__ == "__main__":
    main()
