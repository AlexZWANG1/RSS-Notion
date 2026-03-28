"""Generate a newsletter-quality daily report via LLM (Call 2).

This is the second LLM call in the pipeline.  It receives the structured
tiered JSON produced by Call 1 (interest_scorer.score_items) together with
the original source items, and asks the LLM to write a beautifully
formatted daily report in Notion-flavored markdown.

The function returns a raw markdown string — the caller is responsible for
converting it to Notion blocks or any other delivery format.
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

你的任务：把编辑部传来的分层选题单和原始素材，写成一份可以直接发布的日报。

## 写作原则
1. **像付费 newsletter 编辑一样写**，不是摘要机器人。要有判断、有态度、有节奏。
2. 用 **加粗** 标记关键数字、公司名、转折点——让扫读的人一眼抓住重点。
3. 每条来源文章必须保留原标题和 URL，用 Markdown 链接 `[文字](URL)` 格式。
4. 保持可扫读性：标题层级清晰，段落短，重点前置。
5. 中文为主，术语/专名保留英文原文。

## 输出结构（严格按顺序）

**一句话今日主线**（加粗，一句话概括今天最核心的变化）

---

### 📰 头条

对每个头条事件：

#### 事件标题

200-300 字深度分析。第一句话用加粗概括核心事实，然后展开：
- 发生了什么（关键数字加粗）
- 为什么重要（行业影响）
- 后续值得关注什么

每篇相关原文用 bullet list 列出，格式严格为：
- [来源名](原文URL) **原文标题** — 一句话这篇文章讲了什么

两个头条事件之间用 --- 分隔。

---

### 🔍 值得关注

对每个值得关注的事件：

#### 事件标题

80-100 字摘要。

💡 一句话洞察（这行以💡开头，写具体的洞察内容，不要写"一句话洞察"这四个字）

每篇相关原文：
- [来源名](原文URL) **原文标题** — 一句话摘要

---

### ⚡ 速览

每条格式：
- [来源名](原文URL) **原文标题** — 一句话摘要

---

📊 今日数据：扫描 X 篇 → 聚合 Y 事件 → 精选 Z 条

---

### 📡 值得持续关注的信号

- **信号关键词**：简短解释为什么值得关注
- ...（3-5 条）

## 注意事项
- 所有来源链接必须使用 Markdown 链接语法：`[文字](URL)`
- 来源列表中每篇文章单独一行，不要合并
- 不要编造 URL，只使用提供的原始链接
- 💡 后面直接写洞察内容，不要写"一句话洞察"这个标签
- 如果某个层级没有内容，跳过该段落，不要留空标题\
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
        f"\n## 统计数据（用于 📊 行）\n"
        f"扫描: {total_fetched} 篇, 聚合事件: {events_total}, 精选: {selected_total}\n"
    )

    parts.append(
        f"\n今天是 {date.today().isoformat()}。请按照 system prompt 的结构输出完整日报。"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_daily_report(
    tiered: dict,
    source_items: list,
    config: dict,
) -> str:
    """Generate a newsletter-quality daily report via LLM.

    This is Call 2 in the pipeline.  It takes the tiered dict from Call 1
    (headline / noteworthy / glance / daily_summary) plus the original
    source items list, and returns a formatted Notion-flavored markdown
    string ready for delivery.

    Args:
        tiered: Tiered curation dict produced by ``score_items()``.
        source_items: Original ``SourceItem`` list from the fetch stage.
        config: Pipeline configuration dict (used to read model name).

    Returns:
        Markdown string of the daily report, or an empty string on failure.
    """
    if not tiered:
        logger.warning("generate_daily_report called with empty tiered data")
        return ""

    user_prompt = _build_user_prompt(tiered, source_items)

    model = (
        config.get("pipeline", {})
        .get("llm", {})
        .get("summary_model", "gpt-5.4")
    )

    logger.info(
        "Generating daily report with model=%s  (headline=%d, noteworthy=%d, glance=%d)",
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

    # Use summary_model (typically the stronger model) for report writing
    result = await _call_with_retry(
        client,
        messages,
        model=model,
        temperature=0.5,
        max_retries=2,
    )

    if not result:
        logger.error("Daily report LLM call failed after retries")
        return ""

    # Strip markdown code fences if the model wraps output in ```markdown ... ```
    text = result.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
    if text.endswith("```"):
        text = text[:-3].rstrip()

    logger.info("Daily report generated: %d characters", len(text))
    return text
