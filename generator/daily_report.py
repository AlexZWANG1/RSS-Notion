"""Generate a structured daily report via LLM (Call 2).

This is the second LLM call in the pipeline.  It receives the structured
tiered JSON produced by Call 1 (interest_scorer.score_items) together with
the original source items, and asks the LLM to produce a structured JSON
report with editorial polish — ready for conversion to Notion blocks.
"""

import json
import logging
from datetime import date

from generator.interest_scorer import _get_client, _call_with_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一位顶级科技付费日报的主编。你的读者是忙碌的 AI 产品人和技术决策者——
他们愿意为高密度、有观点的信息付费，但只有 2 分钟阅读时间。

你的任务：把编辑部传来的分层选题单和原始素材，润色为可以直接发布的日报内容。

## 写作原则
1. **像付费 newsletter 编辑一样写**，不是摘要机器人。要有判断、有态度、有节奏。
2. 用 **加粗**（`**文字**` markdown 标记）标记关键数字、公司名、转折点——代码会解析成 Notion bold。
3. 保留原文标题和 URL，不要编造。
4. 中文为主，术语/专名保留英文原文。

## 输出格式
严格输出 JSON，不要用 ```json``` 包裹，不要有其他内容。

{
  "one_liner": "今日主线的一句话概括（有态度、有节奏，像付费 newsletter 标题）",

  "headline": [
    {
      "event_title": "事件标题（编辑润色后）",
      "source_count": 5,
      "analysis": "200-300 字深度分析。第一句话加粗概括核心事实。关键数字用 **加粗**。要有态度和判断。",
      "best_source_url": "最佳来源 URL",
      "best_source_name": "最佳来源名",
      "related_sources": [
        {
          "title": "原文标题（保留原始标题）",
          "url": "https://...",
          "source_name": "OpenAI Blog",
          "channel": "一手/官方",
          "one_liner": "编辑改写的一句话，这篇文章讲了什么"
        }
      ]
    }
  ],

  "noteworthy": [
    {
      "event_title": "事件标题",
      "source_count": 1,
      "summary": "80-100 字摘要，有上下文和具体数字，关键部分 **加粗**",
      "insight": "一句话洞察——具体说出改变了什么判断，不是「值得关注」",
      "best_source_url": "最佳来源 URL",
      "best_source_name": "最佳来源名",
      "related_sources": [
        {
          "title": "原文标题",
          "url": "https://...",
          "source_name": "LangChain Blog",
          "channel": "开源/技术/论文",
          "one_liner": "一句话"
        }
      ]
    }
  ],

  "glance": [
    {
      "title": "原文标题",
      "source_name": "来源名",
      "url": "https://...",
      "channel": "一手/官方",
      "one_liner": "一句话概括"
    }
  ],

  "signals": [
    {
      "keyword": "趋势关键词",
      "note": "为什么值得持续关注（1-2 句话）"
    }
  ]
}

## 各字段要求
- headline: 1-3 个头条事件，每个有深度分析和完整来源列表
- noteworthy: 2-5 个值得关注的事件
- glance: 5-10 条速览
- signals: 3-5 个从全量信息中提炼的趋势信号（这是你的独有价值——编辑视角的趋势判断）
- one_liner: 一句话今日主线，要有态度

## channel 选项（严格使用以下 5 个之一）
- "一手/官方" — 官方博客、产品发布、公司公告
- "深度研究" — 深度分析、调研报告、长文
- "长内容/播客" — YouTube、播客、视频内容
- "社交/社区/Twitter" — Twitter/X、Reddit、社区讨论
- "开源/技术/论文" — GitHub 项目、arXiv 论文、技术文章

## 注意事项
- analysis 和 summary 字段内可以用 **加粗** markdown 标记
- related_sources 必须列出该事件涉及的所有原始文章，不要省略
- 不要编造 URL，只使用提供的原始链接
- insight 后面直接写洞察内容，不要写"一句话洞察"这个标签
- 如果某个层级没有内容，输出空数组 []\
"""


def _build_user_prompt(tiered: dict, source_items: list) -> str:
    """Assemble the user prompt from tiered data and original items."""
    parts: list[str] = []

    # --- Tiered selection from Call 1 ---
    parts.append("## 编辑部选题单（Call 1 输出）\n")
    parts.append("```json")
    # Only include the editorial tiers, not the run_report
    editorial_keys = {
        "headline", "noteworthy", "glance", "daily_summary",
        "events_total", "selected_total",
    }
    editorial = {k: v for k, v in tiered.items() if k in editorial_keys}
    parts.append(json.dumps(editorial, ensure_ascii=False, indent=2))
    parts.append("```\n")

    # --- Original source articles for additional context ---
    if source_items:
        parts.append(f"## 原始素材（共 {len(source_items)} 篇，供补充细节）\n")
        for i, item in enumerate(source_items[:120]):
            title = getattr(item, "title", "") or ""
            url = getattr(item, "url", "") or ""
            source_name = getattr(item, "source_name", "") or ""
            desc = (getattr(item, "description", "") or "")[:300]
            parts.append(
                f"[{i+1}] {title}\n"
                f"来源: {source_name} | URL: {url}\n"
                f"{desc}\n"
            )

    # --- Stats for the footer ---
    total_fetched = len(source_items) if source_items else 0
    events_total = tiered.get("events_total", 0)
    selected_total = tiered.get("selected_total", 0)
    parts.append(
        f"\n## 统计数据\n"
        f"扫描: {total_fetched} 篇, 聚合事件: {events_total}, 精选: {selected_total}\n"
    )

    parts.append(
        f"\n今天是 {date.today().isoformat()}。请按照 system prompt 的 JSON 格式输出完整日报。"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def _parse_report_json(raw: str) -> dict | None:
    """Parse Call 2 JSON response. Returns None on failure."""
    try:
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)

        # Validate required fields
        required = ("headline", "noteworthy", "glance", "one_liner")
        for key in required:
            if key not in data:
                logger.warning("Missing key in Call 2 response: %s", key)
                return None

        # signals is nice-to-have, default to empty
        if "signals" not in data:
            data["signals"] = []

        return data
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to parse Call 2 JSON: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_daily_report(
    tiered: dict,
    source_items: list,
    config: dict,
) -> dict | None:
    """Generate a newsletter-quality daily report via LLM (Call 2).

    Returns structured JSON dict, or None on failure.
    """
    if not tiered:
        logger.warning("generate_daily_report called with empty tiered data")
        return None

    user_prompt = _build_user_prompt(tiered, source_items)

    model = (
        config.get("pipeline", {})
        .get("llm", {})
        .get("summary_model", "gpt-5.4")
    )

    logger.info(
        "Generating daily report v2 (structured JSON) with model=%s  "
        "(headline=%d, noteworthy=%d, glance=%d)",
        model,
        len(tiered.get("headline", [])),
        len(tiered.get("noteworthy", [])),
        len(tiered.get("glance", [])),
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
        temperature=0.5,
        max_retries=2,
    )

    if not result:
        logger.error("Daily report LLM call failed after retries")
        return None

    report = _parse_report_json(result)
    if report:
        logger.info(
            "Daily report v2 parsed: %d headline, %d noteworthy, %d glance, %d signals",
            len(report.get("headline", [])),
            len(report.get("noteworthy", [])),
            len(report.get("glance", [])),
            len(report.get("signals", [])),
        )
    else:
        logger.error("Failed to parse Call 2 response as JSON")

    return report
