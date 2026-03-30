# Daily Report Visual Upgrade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Notion daily report page from markdown-driven flat layout to structured JSON → native Notion blocks (callout, table, bookmark) for a visually rich newsletter experience.

**Architecture:** Call 2 prompt changes from outputting markdown to outputting structured JSON (field names aligned with Call 1). A new `write_daily_report_v2()` function in `notion_writer.py` converts JSON directly to Notion API blocks. `main.py` wires the new flow with fallback to Call 1 data if Call 2 fails.

**Tech Stack:** Python 3.12+, OpenAI API, notion-client, asyncio

**Spec:** User-provided route C spec (conversation context). Reference page: `https://www.notion.so/3311683183e6818aa907fe53d0ad74a5`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `generator/daily_report.py` | Modify | Change Call 2 prompt to output structured JSON; return `dict \| None` instead of `str` |
| `delivery/notion_writer.py` | Modify | Add `write_daily_report_v2()` with callout/table/bookmark block builder |
| `main.py` | Modify | Wire Phase 4a to use new JSON flow + v2 writer |

---

## Chunk 1: Call 2 JSON Output

### Task 1: Change `daily_report.py` — prompt + parser + return type

**Files:**
- Modify: `generator/daily_report.py`

**Key design decisions:**
- Call 2 JSON field names **align with Call 1** (`event_title`, `related_sources`, `one_liner`) so fallback logic in the writer is trivial — same field access works for both Call 1 and Call 2 data.
- `stats` field is **NOT** in the LLM output — pipeline injects real numbers.
- New `signals` field is Call 2's unique value-add (3-5 trend signals extracted from full context).
- `one_liner` field added at top level for the "今日主线" callout.

- [ ] **Step 1: Rewrite `_SYSTEM_PROMPT`**

Replace the entire `_SYSTEM_PROMPT` string. The new prompt keeps all editorial style instructions but changes the output format from markdown to JSON:

```python
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
```

- [ ] **Step 2: Add `_parse_report_json()` function**

Add after `_build_user_prompt()`, before `generate_daily_report()`:

```python
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
```

- [ ] **Step 3: Update `generate_daily_report()` return type and parsing**

Change the function signature and body:

```python
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
```

- [ ] **Step 4: Update module docstring**

Change line 1 docstring from markdown reference to JSON:

```python
"""Generate a structured daily report via LLM (Call 2).

This is the second LLM call in the pipeline.  It receives the structured
tiered JSON produced by Call 1 (interest_scorer.score_items) together with
the original source items, and asks the LLM to produce a structured JSON
report with editorial polish — ready for conversion to Notion blocks.
"""
```

- [ ] **Step 5: Verify file saves correctly**

Run: `python -c "import generator.daily_report; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add generator/daily_report.py
git commit -m "feat(daily_report): Call 2 outputs structured JSON instead of markdown"
```

---

## Chunk 2: Notion v2 Block Builder

### Task 2: Add `write_daily_report_v2()` to `notion_writer.py`

**Files:**
- Modify: `delivery/notion_writer.py`

**Key design decisions:**
- Stats come from function parameters, NOT from LLM output.
- Field access is identical for Call 2 JSON and Call 1 tiered dict (aligned field names).
- When `report` is None, fall back to `tiered` dict seamlessly.
- Blocks are batched in groups of 100 (Notion API limit).
- Reuse existing `_parse_inline_markdown()` for bold/link parsing.
- Bookmark blocks only for whitelisted domains.
- Tables use Notion API native table+table_row blocks.

- [ ] **Step 1: Add constants at module top (after existing constants)**

Add after `_MAX_BLOCK_LEN = 2000` (line 17):

```python
# --- v2 visual upgrade constants ---

_COVER_URL = "https://images.unsplash.com/photo-1677442136019-21780ecad995?w=1200"

BOOKMARK_WHITELIST = {
    "openai.com",
    "anthropic.com",
    "blog.google",
    "deepmind.google",
    "ai.meta.com",
    "huggingface.co",
    "blog.langchain.dev",
    "microsoft.com",
    "apple.com",
}

CHANNEL_EMOJI = {
    "一手/官方": "🔵",
    "深度研究": "🟣",
    "长内容/播客": "🟠",
    "社交/社区/Twitter": "🟢",
    "开源/技术/论文": "⚪",
}
```

- [ ] **Step 2: Add v2 helper functions**

Add before `write_daily_report_v2()`:

```python
# --- v2 block builder helpers ---

def _callout_block(emoji: str, rich_text: list[dict], color: str = "gray_background") -> dict:
    """Build a callout block."""
    return {
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": emoji},
            "rich_text": rich_text,
            "color": color,
        },
    }


def _heading2(text: str) -> dict:
    return {"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _heading3(text: str) -> dict:
    return {"type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _paragraph(rich_text: list[dict]) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text}}


def _bold_text(content: str) -> dict:
    return {"type": "text", "text": {"content": content}, "annotations": {"bold": True}}


def _plain_text(content: str) -> dict:
    return {"type": "text", "text": {"content": content}}


def _link_text(content: str, url: str, bold: bool = False) -> dict:
    rt: dict = {"type": "text", "text": {"content": content, "link": {"url": url}}}
    if bold:
        rt["annotations"] = {"bold": True}
    return rt


def _divider() -> dict:
    return {"type": "divider", "divider": {}}


def _bullet(rich_text: list[dict]) -> dict:
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text}}


def _bookmark_block(url: str) -> dict:
    return {"type": "bookmark", "bookmark": {"url": url}}


def _table_block(width: int, rows: list[list[list[dict]]]) -> list[dict]:
    """Build a table block + its table_row children.

    Args:
        width: Number of columns.
        rows: List of rows. Each row is a list of cells.
              Each cell is a list of rich_text dicts.

    Returns:
        A list containing the table block (with children) — always 1 element.
    """
    table_rows = []
    for row in rows:
        # Pad short rows
        while len(row) < width:
            row.append([_plain_text("")])
        table_rows.append({
            "type": "table_row",
            "table_row": {"cells": row},
        })
    return [{
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }]


def _url_in_whitelist(url: str) -> bool:
    """Check if URL domain is in the bookmark whitelist."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in BOOKMARK_WHITELIST)
    except Exception:
        return False


def _channel_emoji(channel: str) -> str:
    return CHANNEL_EMOJI.get(channel, "⚪")
```

- [ ] **Step 3: Add the main `_build_v2_blocks()` function**

This is the core block builder. It works with both Call 2 JSON and Call 1 tiered dict (same field names):

```python
def _build_v2_blocks(
    report: dict,
    total_fetched: int,
    today: str,
) -> list[dict]:
    """Build Notion blocks for the v2 daily report page.

    Args:
        report: Either Call 2 edited JSON or Call 1 tiered dict (same schema).
        total_fetched: Total articles scanned (from pipeline).
        today: Date string YYYY-MM-DD.

    Returns:
        List of Notion block dicts (may exceed 100 — caller must batch).
    """
    blocks: list[dict] = []

    headlines = report.get("headline", [])
    noteworthies = report.get("noteworthy", [])
    glances = report.get("glance", [])
    signals = report.get("signals", [])
    events_total = report.get("events_total", 0)
    selected_total = report.get("selected_total", len(headlines) + len(noteworthies) + len(glances))

    # === Top stats callout (blue) ===
    stats_line = (
        f"扫描 {total_fetched} 篇 → 聚合 {events_total} 事件 → 精选 {selected_total} 条"
        f" · 📰 头条 {len(headlines)} · 🔍 关注 {len(noteworthies)} · ⚡ 速览 {len(glances)}"
    )
    blocks.append(_callout_block("📊", [_bold_text(stats_line)], "blue_background"))

    # === One-liner callout (yellow) ===
    one_liner = report.get("one_liner") or report.get("daily_summary", "")
    if one_liner:
        blocks.append(_callout_block("📡", _parse_inline_markdown(one_liner), "yellow_background"))

    blocks.append(_divider())

    # === Headlines ===
    if headlines:
        blocks.append(_heading2("📰 头条"))

        for item in headlines:
            title = item.get("event_title", "")
            blocks.append(_heading3(f"🔥 {title}"))

            # Source count + best source line
            sc = item.get("source_count", len(item.get("related_sources", [])))
            best_name = item.get("best_source_name", "")
            source_line = f"🔗 被 {sc} 个来源报道"
            if best_name:
                source_line += f" · 最佳来源：{best_name}"
            blocks.append(_paragraph([_bold_text(source_line)]))

            # Analysis paragraph (parse bold)
            analysis = item.get("analysis", "")
            if analysis:
                blocks.append(_paragraph(_parse_inline_markdown(analysis[:_MAX_BLOCK_LEN])))

            # Sources table (3 columns: 来源 / 标题 / 要点)
            sources = item.get("related_sources", [])
            if sources:
                rows = [
                    [[_bold_text("来源")], [_bold_text("标题")], [_bold_text("要点")]],
                ]
                for src in sources:
                    ch = _channel_emoji(src.get("channel", ""))
                    name = src.get("source_name", "")
                    src_title = src.get("title", "")
                    src_url = src.get("url", "")
                    liner = src.get("one_liner", "")
                    # Cell 1: emoji + name
                    cell1 = [_plain_text(f"{ch} {name}")]
                    # Cell 2: title with link
                    if src_url:
                        cell2 = [_link_text(src_title, src_url)]
                    else:
                        cell2 = [_plain_text(src_title)]
                    # Cell 3: one-liner
                    cell3 = [_plain_text(liner)]
                    rows.append([cell1, cell2, cell3])
                blocks.extend(_table_block(3, rows))

            # Bookmark for best source (if whitelisted)
            best_url = item.get("best_source_url", "")
            if best_url and _url_in_whitelist(best_url):
                blocks.append(_bookmark_block(best_url))

            blocks.append(_divider())

    # === Noteworthy ===
    if noteworthies:
        blocks.append(_heading2("🔍 值得关注"))

        for item in noteworthies:
            title = item.get("event_title", "")
            blocks.append(_heading3(title))

            # Summary paragraph
            summary = item.get("summary", "")
            if summary:
                blocks.append(_paragraph(_parse_inline_markdown(summary[:_MAX_BLOCK_LEN])))

            # Insight callout (yellow)
            insight = item.get("insight", "")
            if insight:
                blocks.append(_callout_block("💡", _parse_inline_markdown(insight), "yellow_background"))

            # Source links (inline, not table — fewer sources here)
            sources = item.get("related_sources", [])
            for src in sources:
                ch = _channel_emoji(src.get("channel", ""))
                name = src.get("source_name", "")
                src_title = src.get("title", "")
                src_url = src.get("url", "")
                liner = src.get("one_liner", "")
                rt: list[dict] = [_plain_text(f"{ch} ")]
                if src_url:
                    rt.append(_link_text(name, src_url))
                else:
                    rt.append(_plain_text(name))
                rt.append(_plain_text(" — "))
                rt.append(_bold_text(src_title))
                if liner:
                    rt.append(_plain_text(f" — {liner}"))
                blocks.append(_paragraph(rt))

        blocks.append(_divider())

    # === Glance table ===
    if glances:
        blocks.append(_heading2("⚡ 速览"))

        rows = [
            [[_bold_text("来源")], [_bold_text("动态")]],
        ]
        for item in glances:
            ch = _channel_emoji(item.get("channel", ""))
            name = item.get("source_name", "")
            liner = item.get("one_liner", "")
            title = item.get("title", "")
            url = item.get("url", "")
            cell1 = [_plain_text(f"{ch} {name}")]
            # Cell 2: one-liner with optional link on title
            if url and title:
                cell2 = _parse_inline_markdown(f"[{title}]({url})" + (f" — {liner}" if liner else ""))
            elif liner:
                cell2 = _parse_inline_markdown(f"**{title}** — {liner}" if title else liner)
            else:
                cell2 = [_plain_text(title)]
            rows.append([cell1, cell2])
        blocks.extend(_table_block(2, rows))

        blocks.append(_divider())

    # === Signals ===
    if signals:
        blocks.append(_heading2("📡 值得持续关注的信号"))
        for sig in signals:
            kw = sig.get("keyword", "")
            note = sig.get("note", "")
            blocks.append(_bullet([_bold_text(kw), _plain_text(f" — {note}")]))
        blocks.append(_divider())

    # === Footer stats callout (blue) ===
    footer = f"来源统计 · 扫描 {total_fetched} 篇 → 聚合 {events_total} 事件 → 精选 {selected_total} 条 · 由 AI Daily Digest 自动生成 · {today}"
    blocks.append(_callout_block("📊", [_bold_text(footer)], "blue_background"))

    return blocks
```

- [ ] **Step 4: Add `write_daily_report_v2()` public function**

```python
async def write_daily_report_v2(
    report: dict | None,
    tiered: dict,
    today: str,
    total_fetched: int,
) -> str | None:
    """Create a visually rich daily report page using native Notion blocks.

    Args:
        report: Call 2 edited JSON (or None to fallback to tiered).
        tiered: Call 1 tiered dict (used as fallback + stats source).
        today: Date string YYYY-MM-DD.
        total_fetched: Total articles scanned.

    Returns:
        Notion page URL, or None on failure.
    """
    notion = _get_notion_client()
    if not notion:
        return None

    # Use Call 2 report if available, otherwise fallback to Call 1 tiered
    data = report if report else tiered
    if not data:
        logger.error("No data for daily report v2")
        return None

    # Merge stats from tiered (authoritative) into data for block builder
    if "events_total" not in data:
        data["events_total"] = tiered.get("events_total", 0)
    if "selected_total" not in data:
        data["selected_total"] = tiered.get("selected_total", 0)
    # Prefer Call 2's one_liner; fallback to Call 1's daily_summary
    if "one_liner" not in data:
        data["one_liner"] = tiered.get("daily_summary", "")

    blocks = _build_v2_blocks(data, total_fetched, today)
    logger.info("Writing daily report v2 with %d blocks", len(blocks))

    title = f"📰 AI Daily — {today}"

    try:
        loop = asyncio.get_running_loop()

        # Create page with first 100 blocks
        page = await loop.run_in_executor(
            None,
            lambda: notion.pages.create(
                parent={"database_id": DATABASE_ID},
                icon={"type": "emoji", "emoji": "🤖"},
                cover={"type": "external", "external": {"url": _COVER_URL}},
                properties={
                    "名称": {"title": [{"text": {"content": title}}]},
                    "来源": {"select": {"name": "系统"}},
                    "重要性": {"select": {"name": "高"}},
                    "收录时间": {"date": {"start": today}},
                },
                children=blocks[:100],
            ),
        )

        page_id = page["id"]
        url = page.get("url", "")

        # Append remaining blocks in batches of 100
        for i in range(100, len(blocks), 100):
            batch = blocks[i : i + 100]
            await loop.run_in_executor(
                None,
                lambda b=batch: notion.blocks.children.append(
                    block_id=page_id, children=b
                ),
            )
            logger.info("Appended blocks %d-%d", i, i + len(batch))

        logger.info("Daily report v2 created: %s", url)
        return url

    except Exception as e:
        logger.error("Failed to create daily report v2: %s", e)
        return None
```

- [ ] **Step 5: Verify file imports and syntax**

Run: `python -c "from delivery.notion_writer import write_daily_report_v2; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add delivery/notion_writer.py
git commit -m "feat(notion_writer): add write_daily_report_v2 with callout/table/bookmark blocks"
```

---

## Chunk 3: Wire main.py + Hub Page

### Task 3: Update `main.py` Phase 4a and hub page

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update Phase 4a imports and flow**

Replace lines 222-237 in `main.py` (the Phase 4a section):

```python
        # 4a: Call 2 — LLM generates structured JSON daily report
        logger.info("  4a: Generating daily report v2 (structured JSON)...")
        report_json = await generate_daily_report(tiered, all_items, config)

        # Write daily report page with native Notion blocks
        from delivery.notion_writer import write_daily_report_v2
        report_url = await write_daily_report_v2(
            report_json, tiered, today, total_fetched=len(all_items)
        )
        if report_url:
            logger.info(f"  Daily report v2: {report_url}")

        # Auto-update 信息流中心 page
        hub_page_id = config.get("notion", {}).get("hub_page_id", "")
        if hub_page_id:
            # Build a simple summary for hub page from available data
            source = report_json if report_json else tiered
            one_liner = source.get("one_liner", source.get("daily_summary", ""))
            headlines_text = " | ".join(
                h.get("event_title", "") for h in source.get("headline", [])
            )
            hub_markdown = f"**{one_liner}**\n\n📰 {headlines_text}"
            from delivery.notion_writer import update_hub_page
            await update_hub_page(hub_page_id, hub_markdown, report_url or "", today)
            logger.info("  Updated 信息流中心 page")
```

- [ ] **Step 2: Update the import at top of file**

The existing import `from delivery.notion_writer import write_daily_report` can be cleaned up. Replace the import block (line 30-36):

```python
from delivery.notion_writer import (
    write_scored_items_to_notion,
    write_daily_report_v2,
    write_run_report_to_notion,
    cleanup_inbox,
    sync_clipper_items,
)
```

And update Phase 4a to use the top-level import instead of inline import:

```python
        # 4a: Call 2 — LLM generates structured JSON daily report
        logger.info("  4a: Generating daily report v2 (structured JSON)...")
        report_json = await generate_daily_report(tiered, all_items, config)

        report_url = await write_daily_report_v2(
            report_json, tiered, today, total_fetched=len(all_items)
        )
        if report_url:
            logger.info(f"  Daily report v2: {report_url}")

        hub_page_id = config.get("notion", {}).get("hub_page_id", "")
        if hub_page_id:
            source = report_json if report_json else tiered
            one_liner = source.get("one_liner", source.get("daily_summary", ""))
            headlines_text = " | ".join(
                h.get("event_title", "") for h in source.get("headline", [])
            )
            hub_markdown = f"**{one_liner}**\n\n📰 {headlines_text}"
            from delivery.notion_writer import update_hub_page
            await update_hub_page(hub_page_id, hub_markdown, report_url or "", today)
            logger.info("  Updated 信息流中心 page")
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import main; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(main): wire Phase 4a to v2 report JSON + block writer"
```

---

## Chunk 4: Integration Test

### Task 4: End-to-end verification

- [ ] **Step 1: Dry-run syntax check**

Run: `python -c "from generator.daily_report import generate_daily_report; from delivery.notion_writer import write_daily_report_v2; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 2: Run pipeline**

Run: `python main.py --skip-email`

Expected logs:
- `Generating daily report v2 (structured JSON)...`
- `Daily report v2 parsed: N headline, N noteworthy, N glance, N signals`
- `Writing daily report v2 with N blocks`
- `Daily report v2 created: https://www.notion.so/...`

- [ ] **Step 3: Open the Notion page and visually verify**

Check for:
- 🤖 page icon + cover image
- Blue callout (📊 stats) at top
- Yellow callout (📡 one-liner) below stats
- Tables with emoji-colored source names in headline section
- 💡 yellow callout for insights in noteworthy section
- 2-column table in glance section
- Signals bullet list
- Blue callout footer

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: v2 daily report visual upgrade — callout/table/bookmark blocks"
```
