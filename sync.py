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

# DRY_RUN=1 只把生成的配置打到 stdout，不读写 gist（改封禁列表时用来先看结果）
DRY_RUN = os.environ.get("DRY_RUN") == "1"

HTTPS_TEST = "https://www.gstatic.com/generate_204"

AI_GROUP = "AI专用"

# 出口被 Gemini API 判为「不支持的地区」的节点（实测返回 400 FAILED_PRECONDITION
# "User location is not supported for the API use"）。这些节点跑普通上网没问题，所以保留在
# proxies 里，但必须从 AI专用 组剔除：url-test 只看 gstatic 延迟，一旦选中它们（往往延迟最低），
# 所有 AI 请求都会被 Google 拒掉，而且会随延迟抖动在「好节点/坏节点」之间来回切，表现为时好时坏。
#
# 实测数据（2026-11-03，逐个节点用 Gemini /v1beta/models 探活）：
#   日本 / 日本[CM]       154.55.127.82   AS18186 Nebula Global     400
#   新加坡 / 新加坡[CM]    154.39.62.242   AS18186 Nebula Global     400
#   美国硅谷3             198.2.206.82    AS54600 PEG TECH          400
# 对照组都 200：新加坡3（Akamai）、硅谷/2/4/5/7（PEG TECH）、洛杉矶/圣何塞（Sharktech）、
# 英国、法国、德国、印尼、韩国、美国ISP。所以不是「机房一律封」，是这些落地 IP 进了 Google 的灰名单。
#
# 名字和 server 后缀各挡一层：机场每天轮换会改节点名和随机前缀（美国硅谷4 -> 美国硅谷7），
# 但落地机房域名后缀（jp / sg2 / u12）是稳定的，改名之后仍然挡得住。
AI_BLOCKED_NODES = (
    "🇯🇵 日本",  # 154.55.127.82（AS18186 Nebula Global）
    "🇯🇵 日本[CM]",  # 同上，jp2 落地，同一出口
    "🇸🇬 新加坡",  # 154.39.62.242（AS18186 Nebula Global）
    "🇸🇬 新加坡[CM]",  # 同上，sg2 落地，同一出口
    "美国硅谷3",  # 198.2.206.82（AS54600 PEG TECH）
    "美国硅谷6",  # 142.0.141.137（AS54600 PEG TECH）
)
AI_BLOCKED_SERVERS = (
    r"\.jp\.oshuawei\.com$",
    r"\.jp2\.oshuawei\.com$",
    r"\.sg\.oshuawei\.com$",
    r"\.sg2\.oshuawei\.com$",
    r"\.u12\.oshuawei\.com$",
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


def warn(message):
    """GitHub Actions 会把这个渲染成一条 annotation，方便在 run 页面直接看到。"""
    print(f"::warning::{message}", file=sys.stderr)


def _blocked_by_server(proxy):
    server = proxy.get("server") or ""
    return any(re.search(pattern, server) for pattern in AI_BLOCKED_SERVERS)


def drop_ai_blocked_nodes(sub):
    """把被封节点从 AI专用 组剔除，返回 (已排除, 订阅里已不存在的封禁条目)。"""
    proxies = {p.get("name"): p for p in sub.get("proxies", []) if isinstance(p, dict)}
    for group in sub.get("proxy-groups", []):
        if group.get("name") != AI_GROUP:
            continue
        # include-all 的组只能靠 exclude-filter 按「节点名」挡，server 规则在这里用不上
        if group.get("include-all"):
            pattern = "|".join(re.escape(n) for n in AI_BLOCKED_NODES)
            existing = group.get("exclude-filter")
            group["exclude-filter"] = f"(?:{existing})|(?:{pattern})" if existing else pattern
            return list(AI_BLOCKED_NODES), []
        kept, removed = [], []
        for name in group.get("proxies") or []:
            if name in AI_BLOCKED_NODES or _blocked_by_server(proxies.get(name, {})):
                removed.append(name)
            else:
                kept.append(name)
        if not kept:
            raise RuntimeError(f"剔除后「{AI_GROUP}」没有剩余节点，检查 AI_BLOCKED_* 配置")
        group["proxies"] = kept
        return removed, [n for n in AI_BLOCKED_NODES if n not in proxies]
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
        warn(f"订阅里已经没有 {missing}，节点可能改名了，请更新 AI_BLOCKED_NODES/AI_BLOCKED_SERVERS")
    if not removed:
        warn("一个已知被封的节点都没排除，AI专用 组可能已经整组不可用（订阅改名了？）")

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

    return sub, removed


def main():
    sub_url = os.environ["SUB_URL"]
    sub = yaml.safe_load(get(sub_url))
    custom = yaml.safe_load(open("custom-rules.txt", encoding="utf-8").read())

    config, removed = build(sub, custom)
    new = yaml.safe_dump(
        config,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )

    if DRY_RUN:
        print(new)
        return 0

    gist_id = os.environ["GIST_ID"]
    gist_token = os.environ["GIST_TOKEN"]

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

    # 封禁列表整体失配说明订阅改过名，此时新配置里的 AI专用 可能又会选到被封节点。
    # 配置已经同步出去了，但仍然要让 CI 红掉，否则会像上次一样悄悄坏掉。
    if not removed:
        print(
            "::error::AI专用 一个已知被封的节点都没排除，请更新 AI_BLOCKED_NODES/AI_BLOCKED_SERVERS 后重跑",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
