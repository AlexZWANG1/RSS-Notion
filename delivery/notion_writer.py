"""Write high-scoring items and research reports to a Notion inbox database."""

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Any, Protocol

import httpx
from notion_client import Client

logger = logging.getLogger(__name__)

DATABASE_ID = "d1da0a02-bb0f-4dfd-a7d0-8cf918e6f23c"

# Maximum characters per Notion rich_text block (API limit is 2000).
_MAX_BLOCK_LEN = 2000


# ---------------------------------------------------------------------------
# Lightweight protocol so this module does not hard-depend on the scorer yet.
# ---------------------------------------------------------------------------
class _SourceItemLike(Protocol):
    title: str
    url: str
    source_name: str
    description: str


class _ScoredItemLike(Protocol):
    original: _SourceItemLike
    include: bool
    channel: str
    importance: str
    what_happened: str
    why_it_matters: str
    score_reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_notion_client() -> Client | None:
    """Return a Notion client or *None* when the token is not configured."""
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        logger.warning("NOTION_TOKEN not set – skipping Notion write.")
        return None
    return Client(auth=token)


def _text_block(text: str) -> dict[str, Any]:
    """Build a single paragraph block dict, truncating to the API limit."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"text": {"content": text[:_MAX_BLOCK_LEN]}}],
        },
    }


def _content_blocks_for_item(item) -> list[dict[str, Any]]:
    """Build children blocks that describe a scored item."""
    blocks: list[dict[str, Any]] = []
    wh = getattr(item, "what_happened", "") or getattr(item, "one_line_summary", "")
    wm = getattr(item, "why_it_matters", "") or getattr(item, "key_insight", "")
    if wh:
        blocks.append(_text_block(f"📌 {wh}"))
    if wm:
        blocks.append(_text_block(f"💡 {wm}"))
    reason = getattr(item, "score_reason", "")
    if reason:
        blocks.append(_text_block(f"✅ 入选理由: {reason}"))
    return blocks


def _content_blocks_for_text(content: str, max_blocks: int = 95) -> list[dict[str, Any]]:
    """Split a long text into paragraph blocks (one per line), capped at max_blocks."""
    blocks: list[dict[str, Any]] = []
    for line in content.split("\n"):
        line = line.strip()
        if line:
            blocks.append(_text_block(line))
            if len(blocks) >= max_blocks:
                blocks.append(_text_block("⚠️ 内容已截断（Notion 限制 100 blocks）"))
                break
    return blocks or [_text_block(content[:_MAX_BLOCK_LEN])]


def _build_item_properties(
    title: str,
    source: str,
    importance: str,
    today: str,
    url: str | None = None,
    media_source: str | None = None,
    summary: str | None = None,
    insight: str | None = None,
    selection_reason: str | None = None,
) -> dict[str, Any]:
    """Build the *properties* dict for a Notion page."""
    # Make title a clickable link to the original article when URL is available
    title_rt: dict[str, Any] = {"content": title}
    if url:
        title_rt["link"] = {"url": url}
    props: dict[str, Any] = {
        "名称": {"title": [{"text": title_rt}]},
        "来源": {"select": {"name": source}},
        "重要性": {"select": {"name": importance}},
        "收录时间": {"date": {"start": today}},
    }
    if selection_reason:
        props["入选理由"] = {"rich_text": [{"text": {"content": selection_reason[:2000]}}]}
    if url:
        props["原文链接"] = {"url": url}
    if media_source:
        props["媒体来源"] = {"rich_text": [{"text": {"content": media_source}}]}
    if summary:
        props["摘要"] = {"rich_text": [{"text": {"content": summary[:2000]}}]}
    if insight:
        props["洞察"] = {"rich_text": [{"text": {"content": insight[:2000]}}]}
    return props


async def _run_sync(func, *args, **kwargs):
    """Run a synchronous function in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _is_duplicate(notion: Client, title: str, url: str | None = None) -> bool:
    """Check whether a page with the same title or URL already exists."""
    try:
        # Check by title
        results = notion.search(query=title, filter={"property": "object", "value": "page"})
        for page in results.get("results", []):
            props = page.get("properties", {})
            # Title match
            name_prop = props.get("名称", {})
            title_parts = name_prop.get("title", [])
            if title_parts:
                existing = "".join(t.get("plain_text", "") for t in title_parts)
                if existing == title:
                    return True
            # URL match — same article even if title differs
            if url:
                url_prop = props.get("原文链接", {})
                existing_url = url_prop.get("url", "")
                if existing_url and existing_url == url:
                    return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Duplicate check failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def write_scored_items_to_notion(items: list, today: str) -> int:
    """Write high-scoring items to the Notion inbox database.

    Args:
        items: List of ScoredItem objects (from generator/interest_scorer.py).
        today: Date string in ``YYYY-MM-DD`` format.

    Returns:
        Number of items successfully written.
    """
    notion = _get_notion_client()
    if notion is None:
        return 0

    written = 0
    for item in items:
        try:
            title = f"[{item.original.source_name}] {item.original.title}"

            # Duplicate check (by title + URL)
            dup = await _run_sync(_is_duplicate, notion, title, item.original.url)
            if dup:
                logger.info("Skipping duplicate: %s", title)
                continue

            # Map LLM channel output to Notion select options (with colors)
            # Map LLM channel to exact Notion select option names
            # Notion DB options: 一手/官方(blue), 深度研究(purple), 长内容/播客(orange),
            #   社交/社区/Twitter(green), 开源/技术/论文(gray), 系统(gray)
            _VALID_CHANNELS = {"一手/官方", "深度研究", "长内容/播客", "社交/社区/Twitter", "开源/技术/论文", "系统"}
            _CHANNEL_MAP = {
                # Legacy names → new names
                "一手/深度研究": "一手/官方",
                "长内容": "长内容/播客",
                "社交/社区": "社交/社区/Twitter",
                "开源/论文": "开源/技术/论文",
            }
            ch = getattr(item, "channel", "") or ""
            ch = _CHANNEL_MAP.get(ch, ch)
            if ch not in _VALID_CHANNELS:
                ch = "开源/技术/论文"

            properties = _build_item_properties(
                title=title,
                source=ch,
                importance=item.importance,
                today=today,
                url=item.original.url,
                media_source=item.original.source_name,
                summary=getattr(item, "what_happened", "") or getattr(item, "one_line_summary", ""),
                insight=getattr(item, "why_it_matters", "") or getattr(item, "key_insight", ""),
                selection_reason=item.score_reason,
            )
            children = _content_blocks_for_item(item)

            await _run_sync(
                notion.pages.create,
                parent={"database_id": DATABASE_ID},
                properties=properties,
                children=children,
            )
            written += 1
            logger.info("Written to Notion: %s", title)

        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write item '%s': %s", getattr(item, "original", item), exc)

    logger.info("Notion write complete – %d/%d items written.", written, len(items))
    return written


async def write_research_report_to_notion(
    title: str, content: str, today: str
) -> bool:
    """Create a research report page in the Notion inbox.

    Args:
        title: Report title (without prefix/date).
        content: Full report body text.
        today: Date string in ``YYYY-MM-DD`` format.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    notion = _get_notion_client()
    if notion is None:
        return False

    page_title = f"[AI研究] {title} ({today})"
    properties = _build_item_properties(
        title=page_title,
        source="一手/深度研究",
        importance="高",
        today=today,
    )
    children = _content_blocks_for_text(content)

    try:
        await _run_sync(
            notion.pages.create,
            parent={"database_id": DATABASE_ID},
            properties=properties,
            children=children,
        )
        logger.info("Research report written to Notion: %s", page_title)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write research report: %s", exc)
        return False


async def write_digest_to_notion(
    selected: list, summary: str, total_items: int, today: str
) -> bool:
    """Write a structured daily digest report to Notion."""
    notion = _get_notion_client()
    if notion is None:
        return False

    page_title = f"[AI日报] {today} AI 产业日报"
    properties = _build_item_properties(
        title=page_title,
        source="系统",
        importance="高",
        today=today,
    )

    # Build structured blocks — directly from selected items, not from summary text
    blocks: list[dict[str, Any]] = []

    def _heading2(t):
        return {"type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": t}}]}}
    def _heading3(t):
        return {"type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": t}}]}}
    def _divider():
        return {"type": "divider", "divider": {}}
    def _linked_bullet(title, url, source_name):
        rt: dict[str, Any] = {"content": title}
        if url:
            rt["link"] = {"url": url}
        return {
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"text": {"content": f"[{source_name}] "}, "annotations": {"color": "gray"}},
                    {"text": rt, "annotations": {"bold": True}},
                ],
            },
        }

    # Header
    blocks.append({
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content":
                f"\u4eca\u65e5\u626b\u63cf {total_items} \u6761\uff0c\u7cbe\u9009 {len(selected)} \u6761"
            }}],
            "icon": {"type": "emoji", "emoji": "\U0001f4ca"},
        },
    })

    # Group by channel
    from collections import defaultdict
    by_channel = defaultdict(list)
    must_read = []
    for s in selected:
        if s.importance == "高":
            must_read.append(s)
        ch = getattr(s, "channel", "") or "开源/论文"
        by_channel[ch].append(s)

    # Must read section (importance=高)
    if must_read:
        blocks.append(_heading2("📌 今日必读"))
        for s in must_read[:5]:
            blocks.append(_linked_bullet(s.original.title, s.original.url, s.original.source_name))
            wh = getattr(s, "what_happened", "")
            wm = getattr(s, "why_it_matters", "")
            if wh:
                blocks.append(_text_block(f"    {wh}"))
            if wm:
                blocks.append(_text_block(f"    💡 {wm}"))
            if len(blocks) >= 85:
                break

    if must_read:
        blocks.append(_divider())

    # Channel sections
    _CHANNEL_ICONS = {
        "一手/深度研究": "\U0001f4f0", "一手/官方": "\U0001f4f0",
        "深度研究": "\U0001f52c",
        "长内容": "\U0001f3ac", "长内容/播客": "\U0001f3ac",
        "社交/社区": "\U0001f4ac", "社交/社区/Twitter": "\U0001f4ac",
        "开源/论文": "\U0001f527", "开源/技术/论文": "\U0001f527",
    }
    for ch_name in ["一手/深度研究", "一手/官方", "深度研究", "长内容", "长内容/播客", "社交/社区", "社交/社区/Twitter", "开源/论文", "开源/技术/论文"]:
        items_in_ch = by_channel.get(ch_name, [])
        if not items_in_ch:
            continue
        icon = _CHANNEL_ICONS.get(ch_name, "📄")
        blocks.append(_heading2(f"{icon} {ch_name} ({len(items_in_ch)}条)"))
        for s in items_in_ch:
            blocks.append(_linked_bullet(s.original.title, s.original.url, s.original.source_name))
            wh = getattr(s, "what_happened", "")
            if wh:
                blocks.append(_text_block(f"    {wh}"))
            if len(blocks) >= 90:
                break
        if len(blocks) >= 90:
            break

    # Part 3: Executive summary as trend observation
    if summary:
        blocks.append(_divider())
        blocks.append(_heading2("\U0001f4c8 \u8d8b\u52bf\u89c2\u5bdf"))
        paras = [p.strip() for p in summary.split("\n") if p.strip()]
        for para in paras[:5]:
            if len(blocks) >= 98:
                break
            blocks.append(_text_block(para))

    blocks = blocks[:98]

    try:
        await _run_sync(
            notion.pages.create,
            parent={"database_id": DATABASE_ID},
            properties=properties,
            children=blocks,
        )
        logger.info("Digest written to Notion: %s", page_title)
        return True
    except Exception as exc:
        logger.error("Failed to write digest: %s", exc)
        return False


async def write_run_report_to_notion(summary: str, today: str) -> bool:
    """Write a pipeline run summary to the Notion inbox.

    Args:
        summary: Plain-text run summary.
        today: Date string in ``YYYY-MM-DD`` format.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    notion = _get_notion_client()
    if notion is None:
        return False

    page_title = f"[运行报告] {today} 信息流处理详情"
    properties = _build_item_properties(
        title=page_title,
        source="系统",
        importance="低",
        today=today,
    )
    children = _content_blocks_for_text(summary)

    try:
        await _run_sync(
            notion.pages.create,
            parent={"database_id": DATABASE_ID},
            properties=properties,
            children=children,
        )
        logger.info("Run report written to Notion: %s", page_title)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write run report: %s", exc)
        return False


# ---------------------------------------------------------------------------
# v2: Daily Report Page
# ---------------------------------------------------------------------------

def _build_daily_report_blocks(tiered: dict, total_fetched: int = 0) -> list[dict]:
    """Build Notion blocks for the tiered daily report page."""
    blocks: list[dict] = []

    # Daily summary callout
    summary = tiered.get("daily_summary", "")
    if summary:
        blocks.append({
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "📡"},
                "rich_text": [{"type": "text", "text": {"content": summary}}],
            }
        })

    # --- Headline section ---
    blocks.append({"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "📰 头条"}}]}})

    for item in tiered.get("headline", []):
        blocks.append({"type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": item.get("event_title", "")}}]}})
        source_line = f"来源：{item.get('best_source_name', '')}"
        if item.get("source_count", 0) > 1:
            source_line += f" | 被 {item['source_count']} 个来源报道"
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": source_line}, "annotations": {"bold": True}}]}})
        analysis = item.get("analysis", "")
        if analysis:
            blocks.append(_text_block(analysis))
        # Related sources with titles + one-liner
        related = item.get("related_sources", [])
        if related:
            for src in related:
                src_title = src.get("title", "")
                src_url = src.get("url", "")
                src_name = src.get("source_name", "")
                one_liner = src.get("one_liner", "")
                prefix = f"[{src_name}] " if src_name else ""
                title_rt: dict[str, Any] = {"content": src_title}
                if src_url:
                    title_rt["link"] = {"url": src_url}
                rich_text: list[dict[str, Any]] = []
                if prefix:
                    rich_text.append({"type": "text", "text": {"content": prefix}, "annotations": {"color": "gray"}})
                rich_text.append({"type": "text", "text": title_rt, "annotations": {"bold": True}})
                if one_liner:
                    rich_text.append({"type": "text", "text": {"content": f" — {one_liner}"}})
                blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text}})
        elif item.get("best_source_url"):
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🔗 {item.get('best_source_name', '原文')}", "link": {"url": item["best_source_url"]}}}]}})
        blocks.append({"type": "divider", "divider": {}})

    # --- Noteworthy section ---
    blocks.append({"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔍 值得关注"}}]}})

    for item in tiered.get("noteworthy", []):
        blocks.append({"type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": item.get("event_title", "")}}]}})
        summary_text = item.get("summary", "")
        if summary_text:
            blocks.append(_text_block(summary_text))
        insight = item.get("insight", "")
        if insight:
            blocks.append(_text_block(f"💡 {insight}"))
        # Related sources with one-liners
        related = item.get("related_sources", [])
        if related:
            for src in related:
                src_title = src.get("title", "")
                src_url = src.get("url", "")
                src_name = src.get("source_name", "")
                one_liner = src.get("one_liner", "")
                prefix = f"[{src_name}] " if src_name else ""
                title_rt: dict[str, Any] = {"content": src_title}
                if src_url:
                    title_rt["link"] = {"url": src_url}
                rich_text: list[dict[str, Any]] = []
                if prefix:
                    rich_text.append({"type": "text", "text": {"content": prefix}, "annotations": {"color": "gray"}})
                rich_text.append({"type": "text", "text": title_rt, "annotations": {"bold": True}})
                if one_liner:
                    rich_text.append({"type": "text", "text": {"content": f" — {one_liner}"}})
                blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text}})
        elif item.get("best_source_url"):
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"🔗 {item.get('best_source_name', '原文')}", "link": {"url": item["best_source_url"]}}}]}})

    blocks.append({"type": "divider", "divider": {}})

    # --- Glance section ---
    blocks.append({"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "⚡ 速览"}}]}})

    for item in tiered.get("glance", []):
        src_name = item.get("source_name", "")
        prefix = f"[{src_name}] " if src_name else ""
        title = item.get("title", "")
        one_liner = item.get("one_liner", "")
        url = item.get("url", "")
        rich_text: list[dict[str, Any]] = []
        if prefix:
            rich_text.append({"type": "text", "text": {"content": prefix}, "annotations": {"color": "gray"}})
        title_rt: dict[str, Any] = {"content": title}
        if url:
            title_rt["link"] = {"url": url}
        rich_text.append({"type": "text", "text": title_rt, "annotations": {"bold": True}})
        if one_liner:
            rich_text.append({"type": "text", "text": {"content": f" — {one_liner}"}})
        blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text}})

    blocks.append({"type": "divider", "divider": {}})

    # --- Stats ---
    events_total = tiered.get("events_total", 0)
    selected_total = tiered.get("selected_total", 0)
    stats = f"📊 来源统计：抓取 {total_fetched} 篇 → 聚合 {events_total} 事件 → 精选 {selected_total} 条"
    blocks.append(_text_block(stats))

    return blocks[:98]


async def write_daily_report(tiered: dict, total_fetched: int = 0, parent_page_id: str = "") -> str | None:
    """Create a daily report page in Notion. Returns page URL or None."""
    notion = _get_notion_client()
    if not notion:
        return None

    today = date.today().isoformat()
    title = f"📰 AI Daily — {today}"

    blocks = _build_daily_report_blocks(tiered, total_fetched)

    try:
        if parent_page_id:
            parent = {"page_id": parent_page_id}
            properties = {"title": {"title": [{"text": {"content": title}}]}}
        else:
            parent = {"database_id": DATABASE_ID}
            properties = {"名称": {"title": [{"text": {"content": title}}]}}
        loop = asyncio.get_running_loop()
        page = await loop.run_in_executor(
            None,
            lambda: notion.pages.create(
                parent=parent,
                properties=properties,
                children=blocks,
            )
        )
        url = page.get("url", "")
        logger.info(f"Daily report created: {url}")
        return url
    except Exception as e:
        logger.error(f"Failed to create daily report: {e}")
        return None


# ---------------------------------------------------------------------------
# v2: Web Clipper Sync
# ---------------------------------------------------------------------------

def _build_clipper_summary_prompt(title: str, url: str, body: str) -> str:
    """Build prompt for summarizing a Web Clipper item."""
    return f"""为以下文章生成摘要和洞察。

标题：{title}
URL：{url}
正文：{body[:2000]}

输出 JSON 格式：
{{"summary": "100-200字摘要", "insight": "一句话核心洞察", "importance": "高/中/低"}}"""


async def sync_clipper_items(config: dict) -> dict:
    """Process new Web Clipper items: fetch content, generate summaries, update Notion."""
    import json as _json
    from sources.content_fetcher import fetch_content
    from generator.interest_scorer import _get_client, _call_with_retry

    db_id = (config or {}).get("notion", {}).get("clipper_database_id", "")
    if not db_id:
        return {"processed": 0, "errors": []}

    notion = _get_notion_client()
    if not notion:
        return {"processed": 0, "errors": []}

    try:
        loop = asyncio.get_running_loop()
        token = os.environ.get("NOTION_TOKEN", "")
        response = await loop.run_in_executor(
            None,
            lambda: httpx.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json={"filter": {"property": "已处理", "checkbox": {"equals": False}}},
                timeout=20.0,
            )
        )
        pages = response.json().get("results", [])
    except Exception as e:
        logger.error(f"Failed to query Web Clipper: {e}")
        return {"processed": 0, "errors": [str(e)]}

    if not pages:
        logger.info("No new Web Clipper items to process")
        return {"processed": 0, "errors": []}

    logger.info(f"Processing {len(pages)} new Web Clipper items")

    processed = 0
    errors = []
    model = (config or {}).get("pipeline", {}).get("llm", {}).get("processing_model", "gpt-5.4-mini")

    for page in pages:
        try:
            props = page.get("properties", {})
            title_arr = props.get("标题", {}).get("title", [])
            title = title_arr[0]["plain_text"] if title_arr else "Untitled"
            url = props.get("userDefined:URL", {}).get("url", "")

            body = ""
            if url:
                body = await fetch_content(url)

            prompt = _build_clipper_summary_prompt(title, url, body)
            client = _get_client()
            messages = [
                {"role": "system", "content": "你是一个文章摘要助手。严格输出JSON。"},
                {"role": "user", "content": prompt},
            ]
            response_text = await _call_with_retry(client, messages, model, temperature=0.3, max_retries=2)

            if response_text:
                try:
                    text = response_text.strip().strip("`")
                    if text.startswith("json"):
                        text = text[4:].strip()
                    data = _json.loads(text)

                    update_props: dict[str, Any] = {}
                    if data.get("summary"):
                        update_props["摘要"] = {"rich_text": [{"text": {"content": data["summary"][:2000]}}]}
                    if data.get("insight"):
                        update_props["洞察"] = {"rich_text": [{"text": {"content": data["insight"][:2000]}}]}
                    if data.get("importance"):
                        update_props["重要性"] = {"select": {"name": data["importance"]}}
                    update_props["来源类型"] = {"select": {"name": "手动剪藏"}}
                    update_props["已处理"] = {"checkbox": True}

                    await loop.run_in_executor(
                        None,
                        lambda pid=page["id"], p=update_props: notion.pages.update(page_id=pid, properties=p)
                    )
                    processed += 1
                    logger.info(f"Clipper item processed: {title}")
                except _json.JSONDecodeError:
                    logger.warning(f"Failed to parse clipper summary for: {title}")
                    errors.append(f"JSON parse error: {title}")
            else:
                errors.append(f"LLM call failed: {title}")

        except Exception as e:
            errors.append(f"{title}: {e}")
            logger.warning(f"Clipper sync error for page {page.get('id')}: {e}")

    logger.info(f"Clipper sync done: processed {processed}, errors {len(errors)}")
    return {"processed": processed, "errors": errors}


# ---------------------------------------------------------------------------
# v2: Simplified Inbox Cleanup — delete all after retention period
# ---------------------------------------------------------------------------

def _query_expired_pages(token: str, retention_days: int = 3) -> list[dict]:
    """Query inbox for pages with 收录时间 older than retention_days."""
    cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "filter": {
                "and": [
                    {"property": "收录时间", "date": {"before": cutoff}},
                    {"property": "待深度阅读", "checkbox": {"equals": False}},
                ],
            },
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


async def cleanup_inbox(retention_days: int = 3) -> dict:
    """Delete all inbox items older than N days. No archiving."""
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        logger.warning("NOTION_TOKEN not set – skipping cleanup.")
        return {"deleted": 0, "errors": []}

    try:
        loop = asyncio.get_running_loop()
        expired = await loop.run_in_executor(
            None, lambda: _query_expired_pages(token, retention_days)
        )
        logger.info(f"Found {len(expired)} expired inbox items (>{retention_days} days)")

        notion = _get_notion_client()
        deleted = 0
        errors = []
        for page in expired:
            try:
                await loop.run_in_executor(
                    None,
                    lambda pid=page["id"]: notion.pages.update(page_id=pid, archived=True)
                )
                deleted += 1
            except Exception as e:
                errors.append(str(e))

        logger.info(f"Cleanup done: deleted {deleted}, errors {len(errors)}")
        return {"deleted": deleted, "errors": errors}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return {"deleted": 0, "errors": [str(e)]}


# ---------------------------------------------------------------------------
# v2: Markdown-based daily report (Call 2 output)
# ---------------------------------------------------------------------------

async def write_daily_report_markdown(markdown: str, today: str) -> str | None:
    """Create a daily report page from LLM-generated markdown. Returns page URL or None."""
    notion = _get_notion_client()
    if not notion:
        return None

    title = f"📰 AI Daily — {today}"

    # Convert markdown to Notion blocks by splitting into paragraphs
    blocks: list[dict] = []
    for line in markdown.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#### "):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": _parse_inline_markdown(line[5:])}})
        elif line.startswith("### "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": _parse_inline_markdown(line[4:])}})
        elif line.startswith("## "):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": _parse_inline_markdown(line[3:])}})
        elif line == "---":
            blocks.append({"type": "divider", "divider": {}})
        elif line.startswith("- "):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _parse_inline_markdown(line[2:])}})
        elif line.startswith("💡"):
            content = line.replace("💡", "", 1).strip()
            blocks.append({"type": "callout", "callout": {"icon": {"type": "emoji", "emoji": "💡"}, "rich_text": _parse_inline_markdown(content)}})
        elif line[0] in "📊📡🔥⚡":
            emoji = line[0]
            content = line.replace(emoji, "", 1).strip()
            blocks.append({"type": "callout", "callout": {"icon": {"type": "emoji", "emoji": emoji}, "rich_text": _parse_inline_markdown(content)}})
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": _parse_inline_markdown(line)}})

    blocks = blocks[:98]

    try:
        loop = asyncio.get_running_loop()
        page = await loop.run_in_executor(
            None,
            lambda: notion.pages.create(
                parent={"database_id": DATABASE_ID},
                properties={
                    "名称": {"title": [{"text": {"content": title}}]},
                    "来源": {"select": {"name": "系统"}},
                    "重要性": {"select": {"name": "高"}},
                    "收录时间": {"date": {"start": today}},
                },
                children=blocks,
            )
        )
        url = page.get("url", "")
        logger.info(f"Daily report (markdown) created: {url}")
        return url
    except Exception as e:
        logger.error(f"Failed to create daily report: {e}")
        return None


def _parse_inline_markdown(text: str) -> list[dict]:
    """Parse inline markdown (**bold**, [link](url)) into Notion rich_text."""
    import re
    rich_text: list[dict] = []
    # Pattern: **bold** or [text](url)
    pattern = re.compile(r'(\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)]+)\))')
    last_end = 0
    for m in pattern.finditer(text):
        # Add text before this match
        if m.start() > last_end:
            plain = text[last_end:m.start()]
            if plain:
                rich_text.append({"type": "text", "text": {"content": plain}})
        if m.group(2):  # **bold**
            rich_text.append({"type": "text", "text": {"content": m.group(2)}, "annotations": {"bold": True}})
        elif m.group(3):  # [text](url)
            rich_text.append({"type": "text", "text": {"content": m.group(3), "link": {"url": m.group(4)}}})
        last_end = m.end()
    # Remaining text
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            rich_text.append({"type": "text", "text": {"content": remaining}})
    if not rich_text:
        rich_text.append({"type": "text", "text": {"content": text}})
    return rich_text


async def update_hub_page(hub_page_id: str, report_markdown: str, report_url: str, today: str) -> bool:
    """Update 信息流中心: blue callout link + red callout content."""
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return False

    try:
        loop = asyncio.get_running_loop()

        # Get existing blocks
        resp = await loop.run_in_executor(
            None,
            lambda: httpx.get(
                f"https://api.notion.com/v1/blocks/{hub_page_id}/children",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
                params={"page_size": 50},
                timeout=20.0,
            )
        )
        if resp.status_code != 200:
            logger.warning(f"Cannot read hub page: {resp.status_code}")
            return False

        blocks = resp.json().get("results", [])
        updated = False

        for block in blocks:
            if block.get("type") != "callout":
                continue
            icon = block.get("callout", {}).get("icon", {}).get("emoji", "")

            if icon == "📰":
                # Blue callout — update report link
                # Notion API can't set mention-page via rich_text patch,
                # so we set a text with the URL
                link_text = f"今日日报（{today}）"
                await loop.run_in_executor(
                    None,
                    lambda bid=block["id"]: httpx.patch(
                        f"https://api.notion.com/v1/blocks/{bid}",
                        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
                        json={"callout": {"rich_text": [
                            {"type": "text", "text": {"content": "📰 ", "link": {"url": report_url}}, "annotations": {"bold": True}},
                            {"type": "text", "text": {"content": link_text, "link": {"url": report_url}}, "annotations": {"bold": True}},
                            {"type": "text", "text": {"content": " · 收件箱 · 配置 — 点击日报链接查看完整内容"}},
                        ]}},
                        timeout=20.0,
                    )
                )
                logger.info("Updated hub blue callout link")
                updated = True

            elif icon == "📡":
                # Red callout — replace content with latest daily report
                # First delete all children of this callout
                children_resp = await loop.run_in_executor(
                    None,
                    lambda bid=block["id"]: httpx.get(
                        f"https://api.notion.com/v1/blocks/{bid}/children",
                        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
                        params={"page_size": 100},
                        timeout=20.0,
                    )
                )
                if children_resp.status_code == 200:
                    for child in children_resp.json().get("results", []):
                        await loop.run_in_executor(
                            None,
                            lambda cid=child["id"]: httpx.delete(
                                f"https://api.notion.com/v1/blocks/{cid}",
                                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
                                timeout=20.0,
                            )
                        )

                # Build new blocks from report markdown
                new_blocks = []
                for line in report_markdown.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("#### "):
                        new_blocks.append({"type": "heading_3", "heading_3": {"rich_text": _parse_inline_markdown(line[5:])}})
                    elif line.startswith("### "):
                        new_blocks.append({"type": "heading_2", "heading_2": {"rich_text": _parse_inline_markdown(line[4:])}})
                    elif line == "---":
                        new_blocks.append({"type": "divider", "divider": {}})
                    elif line.startswith("- "):
                        new_blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _parse_inline_markdown(line[2:])}})
                    elif line.startswith("💡"):
                        content = line.replace("💡", "", 1).strip()
                        new_blocks.append({"type": "callout", "callout": {"icon": {"type": "emoji", "emoji": "💡"}, "rich_text": _parse_inline_markdown(content)}})
                    elif line[0] in "📊📡🔥⚡":
                        emoji = line[0]
                        content = line.replace(emoji, "", 1).strip()
                        new_blocks.append({"type": "callout", "callout": {"icon": {"type": "emoji", "emoji": emoji}, "rich_text": _parse_inline_markdown(content)}})
                    else:
                        new_blocks.append({"type": "paragraph", "paragraph": {"rich_text": _parse_inline_markdown(line)}})

                # Append new blocks in batches of 100
                for i in range(0, min(len(new_blocks), 95), 100):
                    batch = new_blocks[i:i+100]
                    await loop.run_in_executor(
                        None,
                        lambda bid=block["id"], b=batch: httpx.patch(
                            f"https://api.notion.com/v1/blocks/{bid}",
                            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
                            json={"callout": {"rich_text": [{"type": "text", "text": {"content": f"📰 AI Daily — {today}"}}]}},
                            timeout=20.0,
                        )
                    )
                    # Append children
                    await loop.run_in_executor(
                        None,
                        lambda bid=block["id"], b=batch: httpx.patch(
                            f"https://api.notion.com/v1/blocks/{bid}/children",
                            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
                            json={"children": b},
                            timeout=30.0,
                        )
                    )

                logger.info(f"Updated hub red callout with {len(new_blocks)} blocks")
                updated = True

        return updated
    except Exception as e:
        logger.warning(f"Failed to update hub page: {e}")
        return False
