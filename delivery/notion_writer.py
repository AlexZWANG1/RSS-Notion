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
ARCHIVE_DATABASE_ID = "fa9724b4-aa43-48ad-8f43-0f902abd760f"

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
    content_type: str
    source_category: str
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
    score_reason = getattr(item, "score_reason", "")
    if score_reason:
        blocks.append(_text_block(f"✅ 入选理由: {score_reason}"))
    content_type = getattr(item, "content_type", "")
    if content_type:
        blocks.append(_text_block(f"📂 类型: {content_type}"))
    if item.tags:
        blocks.append(_text_block(f"🏷️ Tags: {', '.join(item.tags)}"))
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
    topic: str | None,
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
    if topic:
        props["话题"] = {"multi_select": [{"name": topic}]}
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
            title = f"[AI精选] {item.original.title}"

            # Duplicate check (by title + URL)
            dup = await _run_sync(_is_duplicate, notion, title, item.original.url)
            if dup:
                logger.info("Skipping duplicate: %s", title)
                continue

            # Use LLM-assigned source category, fallback to AI技术社区
            _VALID_CATEGORIES = {
                "科技媒体", "AI技术社区", "论文与评审", "社交/社区/视频",
                "官方一手", "个人分析师", "数据/榜单/基准", "投资机构报告",
                "独立研究机构", "系统", "手动",
            }
            source_label = getattr(item, "source_category", "") or ""
            if source_label not in _VALID_CATEGORIES:
                source_label = "AI技术社区"

            properties = _build_item_properties(
                title=title,
                source=source_label,
                topic=item.topic,
                importance=item.importance,
                today=today,
                url=item.original.url,
                media_source=item.original.source_name,
                summary=item.one_line_summary,
                insight=item.key_insight,
                selection_reason=getattr(item, "score_reason", ""),
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


# ---------------------------------------------------------------------------
# Inbox Cleanup — archive starred, delete the rest after retention period
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


def _extract_page_fields(page: dict) -> dict:
    """Extract key fields from a Notion page for archiving."""
    props = page.get("properties", {})

    title_parts = props.get("名称", {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts)

    url = props.get("原文链接", {}).get("url", "")

    media_parts = props.get("媒体来源", {}).get("rich_text", [])
    media = "".join(t.get("plain_text", "") for t in media_parts)

    source_sel = props.get("来源", {}).get("select")
    source = source_sel["name"] if source_sel else ""

    importance_sel = props.get("重要性", {}).get("select")
    importance = importance_sel["name"] if importance_sel else ""

    topic_opts = props.get("话题", {}).get("multi_select", [])
    topics = [t["name"] for t in topic_opts]

    choice_sel = props.get("选择", {}).get("select")
    choice = choice_sel["name"] if choice_sel else ""

    date_prop = props.get("收录时间", {}).get("date")
    date_start = date_prop["start"] if date_prop else ""

    return {
        "page_id": page["id"],
        "title": title,
        "url": url,
        "media": media,
        "source": source,
        "importance": importance,
        "topics": topics,
        "choice": choice,
        "date_start": date_start,
    }


def _archive_to_database(notion: Client, fields: dict) -> None:
    """Copy a page's key properties into the archive database."""
    # Map inbox 来源 values to archive 来源 values
    _ARCHIVE_SOURCE_MAP = {
        "科技媒体": "AI生成", "AI技术社区": "AI生成", "论文与评审": "AI生成",
        "社交/社区/视频": "AI生成", "官方一手": "AI生成", "个人分析师": "AI生成",
        "数据/榜单/基准": "AI生成", "投资机构报告": "AI生成", "独立研究机构": "AI生成",
        "系统": "系统", "手动": "手动",
    }
    archive_source = _ARCHIVE_SOURCE_MAP.get(fields["source"], "AI生成")

    # Map importance (same values in both DBs)
    _VALID_IMPORTANCE = {"高", "中", "低"}
    importance = fields["importance"] if fields["importance"] in _VALID_IMPORTANCE else "中"

    props: dict[str, Any] = {
        "名称": {"title": [{"text": {"content": fields["title"]}}]},
        "来源": {"select": {"name": archive_source}},
        "状态": {"select": {"name": "已归档"}},
        "重要性": {"select": {"name": importance}},
    }
    if fields["url"]:
        props["原文链接"] = {"url": fields["url"]}
    if fields["media"]:
        props["媒体来源"] = {"rich_text": [{"text": {"content": fields["media"][:2000]}}]}
    if fields["date_start"]:
        props["收录时间"] = {"date": {"start": fields["date_start"]}}
    if fields["topics"]:
        props["话题"] = {"multi_select": [{"name": t} for t in fields["topics"]]}
    if fields["choice"]:
        _VALID_CHOICE = {"收藏", "不收藏"}
        if fields["choice"] in _VALID_CHOICE:
            props["选择"] = {"select": {"name": fields["choice"]}}

    notion.pages.create(parent={"database_id": ARCHIVE_DATABASE_ID}, properties=props)


async def cleanup_inbox(retention_days: int = 3) -> dict:
    """Clean up inbox: archive starred items, delete the rest.

    Args:
        retention_days: Keep items newer than this many days.

    Returns:
        Stats dict with archived/deleted/skipped counts.
    """
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        logger.warning("NOTION_TOKEN not set – skipping cleanup.")
        return {"archived": 0, "deleted": 0, "skipped": 0}

    notion = Client(auth=token)
    stats = {"archived": 0, "deleted": 0, "skipped": 0}

    try:
        pages = await _run_sync(_query_expired_pages, token, retention_days)
    except Exception as exc:
        logger.error("Failed to query expired pages: %s", exc)
        return stats

    if not pages:
        logger.info("Inbox cleanup: nothing to clean (all within %d days)", retention_days)
        return stats

    logger.info("Inbox cleanup: found %d expired pages", len(pages))

    for page in pages:
        fields = _extract_page_fields(page)
        page_id = fields["page_id"]

        try:
            if fields["choice"] == "收藏":
                # Archive: copy to archive DB, then soft-delete from inbox
                await _run_sync(_archive_to_database, notion, fields)
                await _run_sync(notion.pages.update, page_id=page_id, archived=True)
                stats["archived"] += 1
                logger.info("  📦 Archived: %s", fields["title"])
            else:
                # Not starred: just soft-delete
                await _run_sync(notion.pages.update, page_id=page_id, archived=True)
                stats["deleted"] += 1
                logger.info("  🗑️ Deleted: %s", fields["title"])
        except Exception as exc:
            logger.warning("  Failed to process %s: %s", fields["title"], exc)
            stats["skipped"] += 1

    logger.info(
        "Inbox cleanup done: %d archived, %d deleted, %d skipped",
        stats["archived"], stats["deleted"], stats["skipped"],
    )
    return stats
