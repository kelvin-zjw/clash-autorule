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

# AI专用 组的选点节奏：默认一周才按最低延迟重排一次，其余时间粘住当前节点。
#
# mihomo 的 url-test 有两个独立机制，不要混为一谈：
#   1) 定时重排：每 interval 秒把所有节点测一遍，按最低延迟重选（tolerance 是「必须快多少
#      才值得换」的容差，这里设 0，即重排就一定选最快）。
#   2) 失效切换：拨号失败会走 onDialFailed，连续失败达到 max-failed-times 次就立刻触发一次
#      健康检查并重选，不用等下一个 interval。所以 interval 拉长不会把「节点挂了也不切」带进来。
# timeout 同时是健康检查超时和失败计数的时间窗口（默认 5000ms）：两次失败间隔超过窗口就重新
# 计数。留 10s 窗口 + 2 次阈值，静默丢包（超时型）的节点也能在下一次连接时被换掉。
AI_INTERVAL = 7 * 24 * 3600  # 604800s，一周
AI_TIMEOUT = 10000  # ms
AI_MAX_FAILED_TIMES = 2

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


def _blocked_by_server(proxy):
    server = proxy.get("server") or ""
    return any(re.search(pattern, server) for pattern in AI_BLOCKED_SERVERS)


def drop_ai_blocked_nodes(sub):
    """把被封节点从 AI专用 组剔除，返回实际剔除的节点名。"""
    proxies = {p.get("name"): p for p in sub.get("proxies", []) if isinstance(p, dict)}
    for group in sub.get("proxy-groups", []):
        if group.get("name") != AI_GROUP:
            continue
        # include-all 的组只能靠 exclude-filter 按「节点名」挡，server 规则在这里用不上，
        # 改名之后的兜底交给 check_ai_group_clean()
        if group.get("include-all"):
            pattern = "|".join(re.escape(n) for n in AI_BLOCKED_NODES)
            existing = group.get("exclude-filter")
            group["exclude-filter"] = f"(?:{existing})|(?:{pattern})" if existing else pattern
            return []
        kept, removed = [], []
        for name in group.get("proxies") or []:
            if name in AI_BLOCKED_NODES or _blocked_by_server(proxies.get(name, {})):
                removed.append(name)
            else:
                kept.append(name)
        if not kept:
            raise RuntimeError(f"剔除后「{AI_GROUP}」没有剩余节点，检查 AI_BLOCKED_* 配置")
        group["proxies"] = kept
        return removed
    raise RuntimeError(f"proxy-groups 里找不到「{AI_GROUP}」，无法排除被封节点")


def check_ai_group_clean(sub):
    """查 AI专用 组里还会不会出现落地命中封禁列表的节点，返回漏网的节点名。

    这是「订阅已经干净」和「机场改名导致名字过滤失效」这两种情况的唯一可靠区分：
    组里没有可疑节点 → 干净（空列表）；还有 server 后缀命中的节点 → 名字没挡住，要报错。
    按 server 判断而不是按名字，是因为机场改名只改名字，落地机房域名后缀是稳定的。
    """
    proxies = {p.get("name"): p for p in sub.get("proxies", []) if isinstance(p, dict)}
    for group in sub.get("proxy-groups", []):
        if group.get("name") != AI_GROUP:
            continue
        if group.get("include-all"):
            pattern = group.get("exclude-filter") or ""
            try:
                excluded = re.compile(pattern).search if pattern else None
            except re.error as exc:
                raise RuntimeError(f"「{AI_GROUP}」的 exclude-filter 无法编译：{exc}")
            return [
                name
                for name, proxy in proxies.items()
                if _blocked_by_server(proxy) and not (excluded and excluded(name))
            ]
        return [
            name
            for name in group.get("proxies") or []
            if name in AI_BLOCKED_NODES or _blocked_by_server(proxies.get(name, {}))
        ]
    raise RuntimeError(f"proxy-groups 里找不到「{AI_GROUP}」，无法检查是否清洗干净")


def tune_ai_group(sub):
    """把 AI专用 改成「一周重排一次、平时粘住，节点失效才立刻切」。

    这里不改 type：订阅侧给的 url-test 正好就是「选最低延迟」，换成 fallback 就变成
    「按列表顺序选第一个可用的」，不再是延迟最优，所以只调参数。
    """
    for group in sub.get("proxy-groups", []):
        if group.get("name") != AI_GROUP:
            continue
        if group.get("type") != "url-test":
            raise RuntimeError(
                f"「{AI_GROUP}」预期是 url-test，实际是 {group.get('type')}，"
                "定时重排策略不适用，请先确认订阅是否改过组类型"
            )
        group["interval"] = AI_INTERVAL
        group["timeout"] = AI_TIMEOUT
        group["max-failed-times"] = AI_MAX_FAILED_TIMES
        # 容差留 0：一周才重排一次，就是要选当下最快的那个，没必要再要求「快 X ms 才换」
        group.pop("tolerance", None)
        return group
    raise RuntimeError(f"proxy-groups 里找不到「{AI_GROUP}」，无法设置选点节奏")


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
    removed = drop_ai_blocked_nodes(sub)
    print(f"{AI_GROUP}: 排除 {removed or '无'}", file=sys.stderr)

    # 7. 兜底自检：机场改节点名之后，名字过滤失效，只能靠落地域名后缀识别
    leaked = check_ai_group_clean(sub)
    print(f"{AI_GROUP}: 残留命中封禁列表的节点 {leaked or '无'}", file=sys.stderr)

    # 8. AI专用 组的选点节奏：一周重排一次，节点失效时靠拨号失败兜底切换
    tune_ai_group(sub)
    print(
        f"{AI_GROUP}: interval={AI_INTERVAL}s timeout={AI_TIMEOUT}ms max-failed-times={AI_MAX_FAILED_TIMES}",
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

    return sub, leaked


def main():
    sub_url = os.environ["SUB_URL"]
    sub = yaml.safe_load(get(sub_url))
    custom = yaml.safe_load(open("custom-rules.txt", encoding="utf-8").read())

    config, leaked = build(sub, custom)
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

    # 剔除是按节点名做的，机场改名之后会漏；check_ai_group_clean() 按稳定的落地域名后缀
    # 再查一遍，还查得到就说明封禁列表已经失配。配置此时已经同步出去了，但仍然要让 CI 红掉，
    # 否则会像上次一样悄悄坏掉。
    if leaked:
        print(
            f"::error::AI专用 里仍有命中封禁列表的落地节点：{leaked}；"
            "机场可能改了节点名，请更新 AI_BLOCKED_NODES/AI_BLOCKED_SERVERS 后重跑",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
