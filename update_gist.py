import json
import os
import urllib.request

GIST_ID = os.environ["GIST_ID"]
TOKEN = os.environ["GIST_TOKEN"]


def api(path, method="GET", data=None):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    return json.loads(urllib.request.urlopen(req).read())


new = open("config.yaml", encoding="utf-8").read()
old = api(f"/gists/{GIST_ID}")["files"].get("config.yaml", {}).get("content", "")

if old == new:
    print("skip: no change")
else:
    body = json.dumps({"files": {"config.yaml": {"content": new}}}).encode()
    api(f"/gists/{GIST_ID}", method="PATCH", data=body)
    print("gist updated")
