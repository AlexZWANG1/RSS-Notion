"""One-shot: append 📈 市场观察 to today's Notion daily report + Obsidian file."""
from dotenv import load_dotenv
load_dotenv()

import asyncio
from pathlib import Path

from delivery.notion_writer import (
    _heading2, _heading3, _paragraph, _callout_block,
    _plain_text, _bold_text, _divider, _table_block,
    _get_notion_client, _run_sync,
)

PAGE_ID = "33f16831-83e6-8153-aab6-f8e456d75dbf"
TODAY = "2026-04-11"
VAULT_FILE = Path(f"D:/研究空间/AI_Daily/{TODAY}.md")

M7 = [
    ("NVDA",  "$181.19", "+1.73%", False),
    ("GOOGL", "$317.35", "+3.89%", True),
    ("AMZN",  "$220.52", "+3.16%", True),
    ("AAPL",  "$257.45", "+1.56%", False),
    ("MSFT",  "$372.28", "~持平", False),
    ("META",  "$635.80", "+0.23%", False),
    ("TSLA",  "$340.17", "+1.87%", False),
]
SEMIS = [
    ("AVGO",  "$371.55", "—"),
    ("SMH",   "$436.88", "—"),
    ("SOXX",  "$386.60", "+2.10%"),
]
INDICES_NOTE = "SPY $680.65 · QQQ $612.51 · 半导体板块整体反弹（SOXX +2.1%）"

INSIGHTS = [
    ("Anthropic Mythos引爆网安恐慌 — 美联储+财政部紧急召见华尔街CEO",
     "Mythos的攻击性能力让监管层前所未有地召开AI专项金融峰会。网络安全股蒸发2万亿。这是AI能力跨越安全红线的信号性事件。GOOGL +3.9%/AMZN +3.2%或反映市场在Anthropic竞争对手（Google/AWS）阵营的重新定价。"),
    ("Anthropic三连发（Word插件+ultraplan+Managed Agents）追赶OpenAI企业市场",
     "Claude for Word、ultraplan无限思考、Managed Agents三款产品同日推出，直指企业场景。结合CoreWeave数十亿算力大单，Anthropic从「研究实验室」向「企业AI平台」转型加速。若IPO，将是2026年最大科技IPO。"),
    ("Jensen Huang × Lex Fridman：NVIDIA从GPU公司到4万亿全栈AI基础设施",
     "Huang阐述NVIDIA不再只卖芯片而是提供从芯片到数据中心到软件的完整栈。NVDA $181维持韧性，半导体板块SOXX +2.1%反弹。AVGO $371也受益于定制ASIC需求叙事。"),
    ("Sam Altman家遭燃烧瓶袭击 — AI焦虑首次演变为物理暴力",
     "无人受伤但信号极强：公众对AI的恐惧正在从线上舆论溢出到现实暴力。对AI公司的物理安全和公关策略都是新课题。短期不影响股价，但长期影响监管叙事。"),
]

blocks = []
blocks.append(_divider())
blocks.append(_heading2(f"📈 市场观察 ({TODAY} 收盘)"))
blocks.append(_paragraph([_bold_text("指数 · "), _plain_text(INDICES_NOTE)]))

# M7 table
header = [[_plain_text("代码")], [_plain_text("收盘")], [_plain_text("涨跌")]]
rows_data = [header]
for code, price, chg, hot in M7:
    rows_data.append([
        [_bold_text(code) if hot else _plain_text(code)],
        [_plain_text(price)],
        [_bold_text(chg) if hot else _plain_text(chg)],
    ])
blocks.extend(_table_block(3, rows_data))

# Semis table
blocks.append(_paragraph([_bold_text("半导体 · ")]))
header2 = [[_plain_text("代码")], [_plain_text("收盘")], [_plain_text("涨跌")]]
rows2 = [header2]
for code, price, chg in SEMIS:
    rows2.append([
        [_plain_text(code)],
        [_plain_text(price)],
        [_plain_text(chg)],
    ])
blocks.extend(_table_block(3, rows2))

blocks.append(_heading3("今日核心 read"))
for i, (head, body) in enumerate(INSIGHTS, 1):
    blocks.append(_callout_block(
        "💡",
        [_bold_text(f"{i}. {head}"), _plain_text(" — " + body)],
        color="blue_background" if i == 1 else "gray_background",
    ))
blocks.append(_callout_block(
    "⚠️",
    [_plain_text("数据来自 Web Search (CNBC/Yahoo Finance 4月11日)，盘后数据可能微调，仅供参考。")],
    color="yellow_background",
))


async def push():
    client = _get_notion_client()
    if client is None:
        print("ERROR: notion client is None")
        return
    await _run_sync(client.blocks.children.append, block_id=PAGE_ID, children=blocks)
    print(f"  Notion: appended {len(blocks)} blocks")


asyncio.run(push())

# Obsidian markdown
md_lines = []
md_lines.append("\n---\n")
md_lines.append(f"## 📈 市场观察 ({TODAY} 收盘)\n")
md_lines.append(f"**指数** · {INDICES_NOTE}\n")
md_lines.append("**M7 收盘**\n")
md_lines.append("| 代码 | 收盘 | 涨跌 |")
md_lines.append("| --- | --- | --- |")
for code, price, chg, hot in M7:
    c = f"**{code}**" if hot else code
    ch = f"**{chg}**" if hot else chg
    md_lines.append(f"| {c} | {price} | {ch} |")
md_lines.append("")
md_lines.append("**半导体**\n")
md_lines.append("| 代码 | 收盘 | 涨跌 |")
md_lines.append("| --- | --- | --- |")
for code, price, chg in SEMIS:
    md_lines.append(f"| {code} | {price} | {chg} |")
md_lines.append("\n### 今日核心 read\n")
for i, (head, body) in enumerate(INSIGHTS, 1):
    callout_type = "info" if i == 1 else "note"
    md_lines.append(f"> [!{callout_type}] {i}. {head}")
    md_lines.append(f"> {body}\n")
md_lines.append("> [!warning] 数据来自 Web Search (CNBC/Yahoo Finance 4月11日)，盘后数据可能微调，仅供参考。\n")

content = "\n".join(md_lines)
with open(VAULT_FILE, "a", encoding="utf-8") as f:
    f.write(content)
print(f"  Obsidian: appended {len(content)} chars to {VAULT_FILE}")
