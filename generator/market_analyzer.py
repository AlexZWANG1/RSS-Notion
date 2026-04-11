"""Market Analyzer — LLM call that produces the 📈 市场观察 section.

This runs in parallel with the existing score_items (Call 1) and
generate_daily_report (Call 2).  It sees the same pool of source items
plus the structured market quotes from MarketDataSource.
"""

import json
import logging
from datetime import date

from generator.interest_scorer import _get_client, _call_with_retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
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
    {
      "symbol": "NVDA",
      "name": "NVIDIA",
      "price": 128.50,
      "change_pct": -3.2,
      "note": "有明显异动或新闻关联时写一句，没有就留空"
    }
  ],

  "deep_analysis": [
    {
      "theme": "你觉得今天最值得聊的主题",
      "content": "100-200 字。说清楚：发生了什么、可能为什么、如果这样会怎样。"
    }
  ],

  "key_finance_news": [
    {
      "title": "新闻标题",
      "source": "Bloomberg",
      "url": "https://...",
      "one_liner": "一句话概括"
    }
  ]
}

## 字段说明
- price_table: 按涨跌幅排序。note 只在异动明显或有新闻关联时填
- deep_analysis: 1-3 个主题，你自己判断今天什么值得聊。大方向：市场可能在交易什么？为什么？有没有技术新闻跟行情能对上的？
- key_finance_news: 2-3 条，必须有真实 URL\
"""


def _build_user_prompt(
    market_items: list,
    all_items: list,
    tiered: dict | None = None,
) -> str:
    """Build the user prompt with market data + tech news context."""
    parts: list[str] = []

    # --- Market quotes ---
    parts.append("## 今日行情数据\n")
    market_count = 0
    for item in market_items:
        extra = getattr(item, "extra", {}) or {}
        if extra.get("symbol"):
            market_count += 1
            symbol = extra["symbol"]
            name = extra.get("name", symbol)
            price = extra.get("price", 0)
            change_pct = extra.get("change_pct", 0)
            volume = extra.get("volume", 0)
            high = extra.get("high", 0)
            low = extra.get("low", 0)
            tag = "[指数]" if extra.get("is_index") else "[个股]"
            sign = "+" if change_pct >= 0 else ""
            parts.append(
                f"{tag} {symbol} ({name}): ${price:.2f} ({sign}{change_pct:.2f}%) "
                f"| 高 ${high:.2f} 低 ${low:.2f} | 量 {volume:,}"
            )

    if market_count == 0:
        parts.append("（今日无行情数据，请仅基于新闻生成市场观察）\n")

    # --- Finance news (from RSS feeds with category 财经媒体) ---
    parts.append("\n## 财经新闻\n")
    finance_count = 0
    for item in all_items:
        src = getattr(item, "source_name", "")
        extra = getattr(item, "extra", {}) or {}
        cat = extra.get("category", "")
        if cat == "财经媒体" or src in (
            "Bloomberg Tech", "Reuters Tech", "CNBC Tech",
            "Financial Times Tech", "MarketWatch Tech",
        ):
            finance_count += 1
            title = getattr(item, "title", "")
            url = getattr(item, "url", "")
            desc = (getattr(item, "description", "") or "")[:200]
            parts.append(f"- [{src}] {title}\n  URL: {url}\n  {desc}\n")
            if finance_count >= 30:
                break

    if finance_count == 0:
        parts.append("（今日未抓到财经媒体新闻）\n")

    # --- Tech news summary for cross-reference ---
    parts.append(f"\n## 今日技术新闻概览（共 {len(all_items)} 条，供交叉分析）\n")

    # If we have tiered data, use that for a concise summary
    if tiered:
        for h in tiered.get("headline", []):
            parts.append(f"📰 头条: {h.get('event_title', '')}")
        for n in tiered.get("noteworthy", []):
            parts.append(f"🔍 关注: {n.get('event_title', '')}")
        summary = tiered.get("daily_summary", "")
        if summary:
            parts.append(f"\n技术日报主线: {summary}")
    else:
        # Fallback: list top items by title
        for item in all_items[:50]:
            src = getattr(item, "source_name", "")
            title = getattr(item, "title", "")
            parts.append(f"- [{src}] {title}")

    parts.append(
        f"\n今天是 {date.today().isoformat()}。请输出 JSON 格式的市场观察。"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def _parse_market_json(raw: str) -> dict | None:
    """Parse market analyzer JSON response."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)

        required = ("market_pulse", "price_table", "deep_analysis")
        for key in required:
            if key not in data:
                logger.warning("Missing key in market analysis: %s", key)
                return None

        if "key_finance_news" not in data:
            data["key_finance_news"] = []

        return data
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to parse market analysis JSON: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_market(
    market_items: list,
    all_items: list,
    config: dict,
    tiered: dict | None = None,
) -> dict | None:
    """Generate market observation section via LLM.

    Args:
        market_items: SourceItems from MarketDataSource.
        all_items: All items from all sources (for cross-reference).
        config: Pipeline config.
        tiered: Optional tiered output from score_items (for headline context).

    Returns:
        Structured dict with market_pulse, price_table, deep_analysis,
        key_finance_news — or None on failure.
    """
    user_prompt = _build_user_prompt(market_items, all_items, tiered)

    model = (
        config.get("pipeline", {})
        .get("llm", {})
        .get("processing_model", "gpt-5.4")
    )

    logger.info(
        "Generating market analysis with model=%s (market_items=%d, all_items=%d)",
        model, len(market_items), len(all_items),
    )

    client = _get_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = await _call_with_retry(
        client,
        messages,
        model=model,
        temperature=0.4,
        max_retries=2,
    )

    if not result:
        logger.error("Market analysis LLM call failed")
        return None

    analysis = _parse_market_json(result)
    if analysis:
        logger.info(
            "Market analysis: %d price rows, %d analysis themes, %d finance news",
            len(analysis.get("price_table", [])),
            len(analysis.get("deep_analysis", [])),
            len(analysis.get("key_finance_news", [])),
        )
    else:
        logger.error("Failed to parse market analysis JSON")

    return analysis
