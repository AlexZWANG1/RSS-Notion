#!/usr/bin/env python3
"""Render tiered.json to Obsidian markdown for 2026-05-06."""
import json
from pathlib import Path

DATE = "2026-05-06"
OBSIDIAN_PATH = Path(f"/Users/项目开发/研究空间_V2/AI_Daily/{DATE}.md")

with open(f"output/{DATE}/tiered.json") as f:
    d = json.load(f)

report = d["report"]
tiered = d["tiered"]


def src_line(s):
    """Format a related source as a markdown line."""
    return f"- [{s['source_name']} ({s['channel']})]({s['url']}): {s['title']} — {s['one_liner']}"


lines = []
lines.append(f"# AI Daily {DATE}")
lines.append("")
lines.append(f"> {report['one_liner']}")
lines.append("")
lines.append(tiered["daily_summary"])
lines.append("")

# ============================================================
# 头条
# ============================================================
lines.append("## 📰 头条")
lines.append("")
for h in report["headline"]:
    lines.append(f"### {h['event_title']}")
    lines.append("")
    lines.append(h["analysis"])
    lines.append("")
    lines.append("**Source**:")
    for s in h["related_sources"]:
        lines.append(src_line(s))
    lines.append("")

# ============================================================
# 值得关注
# ============================================================
lines.append("## 🔍 值得关注")
lines.append("")
for n in report["noteworthy"]:
    lines.append(f"### {n['event_title']}")
    lines.append("")
    lines.append(n["summary"])
    lines.append("")
    lines.append(f"> [!tip] 💡 Insight")
    lines.append(f"> {n['insight']}")
    lines.append("")
    lines.append("**Source**:")
    for s in n["related_sources"]:
        lines.append(src_line(s))
    lines.append("")

# ============================================================
# 速览
# ============================================================
lines.append("## ⚡ 速览")
lines.append("")
lines.append("| 标题 | 来源 | 频道 | 一句话 |")
lines.append("| --- | --- | --- | --- |")
for g in report["glance"]:
    title = g["title"].replace("|", "\\|")
    one_liner = g["one_liner"].replace("|", "\\|")
    lines.append(f"| [{title}]({g['url']}) | {g['source_name']} | {g['channel']} | {one_liner} |")
lines.append("")

# ============================================================
# 信号雷达
# ============================================================
lines.append("## 📡 信号雷达")
lines.append("")
for s in report["signals"]:
    lines.append(f"- **{s['keyword']}**:{s['note']}")
lines.append("")

# ============================================================
# 播客/视频全量速览
# ============================================================
lines.append("## 🎧 播客/视频全量速览")
lines.append("")
lines.append(f"今日 {len(report['podcast_results'])} 条 YouTube/播客内容(按来源分组):")
lines.append("")

from collections import defaultdict
by_src = defaultdict(list)
for p in report["podcast_results"]:
    by_src[p["source_name"]].append(p)

for src in sorted(by_src.keys()):
    plist = by_src[src]
    lines.append(f"### 📺 {src} ({len(plist)})")
    lines.append("")
    for p in plist:
        title = p["title"]
        summary = p["summary"][:200] if p["summary"] else ""
        lines.append(f"- [{title}]({p['url']})")
        if summary:
            lines.append(f"  - {summary}")
    lines.append("")

# ============================================================
# Run report
# ============================================================
lines.append("---")
lines.append("")
lines.append(f"> [!note] 编辑反思")
lines.append(f"> {tiered['run_report']}")
lines.append("")
lines.append(f"> [!info] 本期数据")
lines.append(f"> 抓取 {tiered['events_total']} 条 → 精选 {tiered['selected_total']} 条({len(report['headline'])} 头条 / {len(report['noteworthy'])} noteworthy / {len(report['glance'])} 速览)。")
lines.append("")

OBSIDIAN_PATH.parent.mkdir(parents=True, exist_ok=True)
with OBSIDIAN_PATH.open("w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"✓ Wrote {OBSIDIAN_PATH}")
print(f"  Lines: {len(lines)}")
print(f"  Bytes: {OBSIDIAN_PATH.stat().st_size}")
