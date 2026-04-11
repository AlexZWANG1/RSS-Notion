"""Dry-run test: re-run Call 2 and Call 3 with tweaked prompts, print output only."""

import asyncio
import json
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from generator.interest_scorer import _get_client, _call_with_retry


SYSTEM_CALL2 = """\
你是一位科技日报编辑。你的读者是基金经理和技术决策者。

## 写作原则
1. 说人话。禁止学术腔、禁止抒情、禁止夸张修辞。写出来要像微信群里一个聪明朋友给你发的消息。
2. 事实优先：先说发生了什么（谁、做了什么、具体数字），再用一句话说为什么重要。
3. analysis 控制在 100-150 字。多一个字都是废话。
4. 禁止以下套路：「从X转向Y」「这不仅是…更是…」「值得关注的是」「信号意义在于」「换句话说」。直接说结论。
5. 用 **加粗** 标记关键数字和公司名。中文为主，术语保留英文。
6. 保留原文标题和 URL，不要编造。

## 输出格式
严格输出 JSON，不要用 ```json``` 包裹。

{
  "one_liner": "15字以内，今天最重要的一件事",

  "headline": [
    {
      "event_title": "简洁事件名",
      "source_count": 5,
      "analysis": "100-150字。第一句说事实，第二句说影响，第三句给你的判断。不要铺垫。",
      "best_source_url": "...",
      "best_source_name": "...",
      "related_sources": [
        {"title": "原文标题", "url": "...", "source_name": "...", "channel": "...", "one_liner": "10字内"}
      ]
    }
  ],

  "noteworthy": [
    {
      "event_title": "...",
      "source_count": 1,
      "summary": "50-80字，只说事实和数字",
      "insight": "一句判断，不要用'值得关注'",
      "best_source_url": "...",
      "best_source_name": "...",
      "related_sources": [{"title": "...", "url": "...", "source_name": "...", "channel": "...", "one_liner": "10字内"}]
    }
  ],

  "glance": [
    {"title": "...", "source_name": "...", "url": "...", "channel": "...", "one_liner": "10字内"}
  ],

  "signals": [
    {"keyword": "趋势词", "note": "一句话，为什么你觉得这个趋势会持续"}
  ]
}

## channel 选项（严格使用以下 5 个之一）
- "一手/官方"
- "深度研究"
- "长内容/播客"
- "社交/社区/Twitter"
- "开源/技术/论文"\
"""


SYSTEM_CALL3 = """\
你是一位美股科技板块分析师，服务一位关注 AI/科技赛道的基金经理。

结合行情数据和技术新闻，写一份简短的「市场观察」。

## 写作原则
1. 说人话，少废话。先摆数字，再说你觉得为什么。
2. 你能同时看到技术新闻和股价——尽量把两者串起来，但串不上的别硬串。
3. 多假设少定论。「可能」「如果…则…」比「必将」好。
4. 中文为主，公司名/术语保留英文。关键数字用 **加粗**。

## 输出格式
严格输出 JSON，不要用 ```json``` 包裹。

{
  "market_pulse": "2-3 句，今天大盘怎么样、科技板块怎么样、你觉得在交易什么",

  "price_table": [
    {"symbol": "NVDA", "name": "NVIDIA", "price": 128.50, "change_pct": -3.2, "note": "有明显异动或新闻关联时写一句，没有就留空"}
  ],

  "deep_analysis": [
    {
      "theme": "你觉得今天最值得聊的主题",
      "content": "100-200 字。说清楚：发生了什么、可能为什么、如果这样会怎样。"
    }
  ],

  "key_finance_news": [
    {"title": "...", "source": "Bloomberg", "url": "...", "one_liner": "一句话"}
  ]
}

## 字段说明
- price_table: 按涨跌幅排序。note 只在异动明显或有新闻关联时填
- deep_analysis: 1-3 个主题，你自己判断今天什么值得聊。大方向：市场可能在交易什么？为什么？有没有技术新闻跟行情能对上的？
- key_finance_news: 2-3 条，必须有真实 URL\
"""


async def main():
    with open("output/2026-04-05/data.json", encoding="utf-8") as f:
        d = json.load(f)
    tiered = d["tiered"]

    client = _get_client()
    model = "gpt-5.4"

    # === Call 2: 日报润色 ===
    editorial_keys = {"headline", "noteworthy", "glance", "daily_summary", "events_total", "selected_total"}
    editorial = {k: v for k, v in tiered.items() if k in editorial_keys}

    user2_parts = [
        "## 编辑部选题单（Call 1 输出）\n",
        "```json",
        json.dumps(editorial, ensure_ascii=False, indent=2),
        "```\n",
        "今天是 2026-04-05。按 system prompt 的 JSON 格式输出完整日报。",
    ]
    user2 = "\n".join(user2_parts)

    print(">>> Calling Call 2 (tweaked daily report prompt)...")
    result2 = await _call_with_retry(
        client,
        [{"role": "system", "content": SYSTEM_CALL2}, {"role": "user", "content": user2}],
        model=model, temperature=0.4, max_retries=1,
    )

    if result2:
        text = result2.strip()
        if text.startswith("```"): text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
        if text.startswith("json"): text = text[4:].strip()
        try:
            r = json.loads(text)
            print("\n" + "=" * 60)
            print("CALL 2: 日报润色（调整后）")
            print("=" * 60)
            print(f"\none_liner: {r.get('one_liner', '')}\n")
            for h in r.get("headline", []):
                print(f"📰 {h['event_title']}")
                print(f"   {h.get('analysis', '')}")
                print()
            for n in r.get("noteworthy", []):
                print(f"🔍 {n['event_title']}")
                print(f"   {n.get('summary', '')}")
                print(f"   💡 {n.get('insight', '')}")
                print()
            print("⚡ 速览:")
            for g in r.get("glance", []):
                print(f"   {g.get('title','')} — {g.get('one_liner','')}")
            print()
            if r.get("signals"):
                print("📡 信号:")
                for s in r["signals"]:
                    print(f"   {s['keyword']} — {s.get('note','')}")
        except json.JSONDecodeError as e:
            print(f"Parse error: {e}")
            print(text[:2000])

    # === Call 3: 市场分析 ===
    # Build market context from tiered data
    user3_parts = [
        "## 今日行情数据\n",
        "（今日为周六，无实时行情数据。请基于本周已有信息和财经新闻生成分析。）\n",
        "\n## 财经新闻（从 RSS 抓取）\n",
    ]
    # Extract finance-related items from tiered
    for h in tiered.get("headline", []):
        for src in h.get("related_sources", []):
            user3_parts.append(f"- [{src.get('source_name','')}] {src.get('title','')} — {src.get('one_liner','')}")
    for n in tiered.get("noteworthy", []):
        for src in n.get("related_sources", []):
            user3_parts.append(f"- [{src.get('source_name','')}] {src.get('title','')} — {src.get('one_liner','')}")

    user3_parts.append("\n## 今日技术新闻概览\n")
    for h in tiered.get("headline", []):
        user3_parts.append(f"📰 头条: {h.get('event_title', '')}")
    for n in tiered.get("noteworthy", []):
        user3_parts.append(f"🔍 关注: {n.get('event_title', '')}")
    summary = tiered.get("daily_summary", "")
    if summary:
        user3_parts.append(f"\n技术日报主线: {summary}")

    user3_parts.append("\n今天是 2026-04-05。请输出 JSON 格式的市场观察。")
    user3 = "\n".join(user3_parts)

    print("\n\n>>> Calling Call 3 (tweaked market analysis prompt)...")
    result3 = await _call_with_retry(
        client,
        [{"role": "system", "content": SYSTEM_CALL3}, {"role": "user", "content": user3}],
        model=model, temperature=0.4, max_retries=1,
    )

    if result3:
        text = result3.strip()
        if text.startswith("```"): text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
        if text.startswith("json"): text = text[4:].strip()
        try:
            r = json.loads(text)
            print("\n" + "=" * 60)
            print("CALL 3: 市场分析（调整后）")
            print("=" * 60)
            print(f"\nmarket_pulse: {r.get('market_pulse', '')}\n")
            if r.get("price_table"):
                print("行情表:")
                for row in r["price_table"]:
                    pct = row.get("change_pct") or 0
                    sign = "+" if pct >= 0 else ""
                    price = row.get("price") or 0
                    note = row.get("note") or ""
                    print(f"   {row['symbol']} ${price} ({sign}{pct:.1f}%) {note}")
                print()
            for item in r.get("deep_analysis", []):
                print(f"💡 {item.get('theme', '')}")
                print(f"   {item.get('content', '')}")
                print()
            if r.get("key_finance_news"):
                print("📰 关键财经新闻:")
                for n in r["key_finance_news"]:
                    print(f"   [{n.get('source','')}] {n.get('title','')} — {n.get('one_liner','')}")
        except json.JSONDecodeError as e:
            print(f"Parse error: {e}")
            print(text[:2000])


asyncio.run(main())
