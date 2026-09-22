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

for g in sub["proxy-groups"]:
    if g.get("name") == "Ghelper":
        g["type"] = "url-test"
        g["url"] = "http://www.gstatic.com/generate_204"
        g["interval"] = 300
        g["tolerance"] = 50
        g["include-all"] = True
        g["proxies"] = ["DIRECT", "🌐 全球智能", "AI专用"]

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
