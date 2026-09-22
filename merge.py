import pathlib

import yaml

sub = yaml.safe_load(pathlib.Path("sub.yaml").read_text(encoding="utf-8"))
custom = yaml.safe_load(pathlib.Path("custom-rules.txt").read_text(encoding="utf-8"))

# 自定义 AI 规则置顶
sub["rules"] = custom + sub.get("rules", [])

# Ghelper 组改为自动测速，且把「直连」作为一条候选线路：
# 香港等能直连的地区 -> DIRECT 最快 -> 自动直连
# 内地直连失败 -> 自动选最快节点
for g in sub["proxy-groups"]:
    if g.get("name") == "Ghelper":
        g["type"] = "url-test"
        g["url"] = "http://www.gstatic.com/generate_204"
        g["interval"] = 300
        g["tolerance"] = 50
        g["include-all"] = True
        g["proxies"] = ["DIRECT", "🌐 全球智能", "AI专用"]

pathlib.Path("config.yaml").write_text(
    yaml.safe_dump(
        sub,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    ),
    encoding="utf-8",
)
