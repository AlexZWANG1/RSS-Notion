"""Write high-scoring items and research reports to a Notion inbox database."""

import asyncio
import logging
import os
from typing import Any, Protocol

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
    score: int
    topic: str
    importance: str
    one_line_summary: str
    key_insight: str
    tags: list[str]


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


def _content_blocks_for_item(item: _ScoredItemLike) -> list[dict[str, Any]]:
    """Build children blocks that describe a scored item."""
    blocks: list[dict[str, Any]] = []
    if item.one_line_summary:
        blocks.append(_text_block(f"📌 {item.one_line_summary}"))
    if item.key_insight:
        blocks.append(_text_block(f"💡 {item.key_insight}"))
    if item.tags:
        blocks.append(_text_block(f"🏷️ Tags: {', '.join(item.tags)}"))
    return blocks


def _content_blocks_for_text(content: str) -> list[dict[str, Any]]:
    """Split a long text into paragraph blocks (one per line)."""
    blocks: list[dict[str, Any]] = []
    for line in content.split("\n"):
        line = line.strip()
        if line:
            blocks.append(_text_block(line))
    return blocks or [_text_block(content[:_MAX_BLOCK_LEN])]


def _build_item_properties(
    title: str,
    source: str,
    topic: str | None,
    importance: str,
    today: str,
    url: str | None = None,
    media_source: str | None = None,
) -> dict[str, Any]:
    """Build the *properties* dict for a Notion page."""
    props: dict[str, Any] = {
        "名称": {"title": [{"text": {"content": title}}]},
        "来源": {"select": {"name": source}},
        "状态": {"select": {"name": "未读"}},
        "重要性": {"select": {"name": importance}},
        "收录时间": {"date": {"start": today}},
    }
    if topic:
        props["话题"] = {"multi_select": [{"name": topic}]}
    if url:
        props["原文链接"] = {"url": url}
    if media_source:
        props["媒体来源"] = {"rich_text": [{"text": {"content": media_source}}]}
    return props


async def _run_sync(func, *args, **kwargs):
    """Run a synchronous function in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _is_duplicate(notion: Client, title: str) -> bool:
    """Check whether a page with the exact same title already exists."""
    try:
        results = notion.search(query=title, filter={"property": "object", "value": "page"})
        for page in results.get("results", []):
            props = page.get("properties", {})
            name_prop = props.get("名称", {})
            title_parts = name_prop.get("title", [])
            if title_parts:
                existing = "".join(t.get("plain_text", "") for t in title_parts)
                if existing == title:
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
            title = f"[AI精选] {item.original.title}"

            # Duplicate check
            dup = await _run_sync(_is_duplicate, notion, title)
            if dup:
                logger.info("Skipping duplicate: %s", title)
                continue

            # Determine source label
            source_label = "AI生成" if item.original.source_name in ("AI生成", "research") else "RSS精选"

            properties = _build_item_properties(
                title=title,
                source=source_label,
                topic=item.topic,
                importance=item.importance,
                today=today,
                url=item.original.url,
                media_source=item.original.source_name,
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
    title: str, content: str, topic: str, today: str
) -> bool:
    """Create a research report page in the Notion inbox.

    Args:
        title: Report title (without prefix/date).
        content: Full report body text.
        topic: One of the valid 话题 options.
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
        source="AI生成",
        topic=topic,
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
        topic=None,
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
