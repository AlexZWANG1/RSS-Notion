"""Write daily report data to an Obsidian vault as a markdown file.

Pure file I/O — no Obsidian REST API, no plugin needed. Obsidian will
pick up the file the next time the vault is opened/refreshed.

Vault layout:
    <vault_root>/AI_Daily/<YYYY-MM-DD>.md

Each file embeds the full daily report with:
- YAML frontmatter (date, stats, tags) for Dataview/Bases queries
- H2 sections per tier (headline / noteworthy / glance)
- Obsidian callouts for analysis/insight blocks
- Source links collapsed under each event
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date as _date
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _yaml_escape(value: str) -> str:
    """Escape a string for YAML frontmatter (quoted form)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _frontmatter(date_str: str, stats: dict[str, Any]) -> str:
    """Build YAML frontmatter block."""
    return (
        "---\n"
        f"date: {date_str}\n"
        "type: ai-daily-report\n"
        f"fetched: {stats.get('total_fetched', 0)}\n"
        f"headline: {stats.get('headline', 0)}\n"
        f"noteworthy: {stats.get('noteworthy', 0)}\n"
        f"glance: {stats.get('glance', 0)}\n"
        "tags:\n"
        "  - ai-daily\n"
        "  - rss-notion\n"
        "---\n\n"
    )


def _format_headline(item: dict[str, Any], idx: int) -> str:
    """Format a headline (大事件) entry."""
    title = item.get("event_title", "Untitled")
    analysis = item.get("analysis", "").strip()
    source_count = item.get("source_count", 0)
    best_url = item.get("best_source_url", "")
    best_name = item.get("best_source_name", "")
    related = item.get("related_sources", []) or []

    out: list[str] = []
    out.append(f"### {idx}. {title}")
    out.append("")
    if best_url:
        out.append(f"**主源**: [{best_name}]({best_url}) · 共 {source_count} 个信息源")
        out.append("")
    if analysis:
        out.append("> [!abstract] 分析")
        for line in analysis.splitlines():
            out.append(f"> {line}")
        out.append("")
    if related:
        out.append("**相关信息源**:")
        for src in related:
            t = src.get("title", "")
            u = src.get("url", "")
            sn = src.get("source_name", "")
            ch = src.get("channel", "")
            ol = src.get("one_liner", "")
            line = f"- [{t}]({u}) — *{sn}*"
            if ch:
                line += f" · {ch}"
            out.append(line)
            if ol:
                out.append(f"  - {ol}")
        out.append("")
    return "\n".join(out)


def _format_noteworthy(item: dict[str, Any], idx: int) -> str:
    """Format a noteworthy (值得关注) entry."""
    title = item.get("event_title", "Untitled")
    summary = item.get("summary", "").strip()
    insight = item.get("insight", "").strip()
    source_count = item.get("source_count", 0)
    best_url = item.get("best_source_url", "")
    best_name = item.get("best_source_name", "")
    related = item.get("related_sources", []) or []

    out: list[str] = []
    out.append(f"### {idx}. {title}")
    out.append("")
    if best_url:
        out.append(f"**主源**: [{best_name}]({best_url}) · {source_count} 个信息源")
        out.append("")
    if summary:
        out.append(summary)
        out.append("")
    if insight:
        out.append("> [!tip] 洞察")
        for line in insight.splitlines():
            out.append(f"> {line}")
        out.append("")
    if related:
        out.append("**相关信息源**:")
        for src in related:
            t = src.get("title", "")
            u = src.get("url", "")
            sn = src.get("source_name", "")
            line = f"- [{t}]({u}) — *{sn}*"
            out.append(line)
        out.append("")
    return "\n".join(out)


def _format_glance(item: dict[str, Any], idx: int) -> str:
    """Format a glance (一眼扫) entry — single line per item."""
    title = item.get("title", "Untitled")
    url = item.get("url", "")
    source_name = item.get("source_name", "")
    channel = item.get("channel", "")
    one_liner = item.get("one_liner", "")

    line = f"{idx}. [{title}]({url}) — *{source_name}*"
    if channel:
        line += f" · {channel}"
    if one_liner:
        line += f"\n   - {one_liner}"
    return line


def write_daily_report_obsidian(
    vault_root: str | Path,
    data: dict[str, Any],
    report_date: str | None = None,
) -> Path:
    """Write a daily report data dict as a markdown file in the vault.

    Args:
        vault_root: Vault root directory (e.g. ``D:/研究空间``).
        data: Parsed ``output/<date>/data.json`` content.
        report_date: ``YYYY-MM-DD``. Defaults to ``data['date']`` then today.

    Returns:
        Absolute path to the written markdown file.
    """
    vault = Path(vault_root)
    target_dir = vault / "AI_Daily"
    target_dir.mkdir(parents=True, exist_ok=True)

    date_str = report_date or data.get("date") or _date.today().isoformat()
    target = target_dir / f"{date_str}.md"

    tiered = data.get("tiered", {}) or {}
    stats = data.get("stats", {}) or {}
    daily_summary = (data.get("daily_summary") or "").strip()

    # Legacy format fallback (pre-v2 pipeline: flat items + executive_summary)
    if not tiered and ("items" in data or "executive_summary" in data):
        return _write_legacy_report(target, date_str, data)

    headlines = tiered.get("headline", []) or []
    noteworthies = tiered.get("noteworthy", []) or []
    glances = tiered.get("glance", []) or []

    parts: list[str] = []
    parts.append(_frontmatter(date_str, stats))
    parts.append(f"# AI Daily — {date_str}\n")

    if daily_summary:
        parts.append("## 今日要点\n")
        parts.append(daily_summary + "\n")

    if headlines:
        parts.append(f"## 🔥 头条事件 ({len(headlines)})\n")
        for i, item in enumerate(headlines, 1):
            parts.append(_format_headline(item, i))

    if noteworthies:
        parts.append(f"## 📌 值得关注 ({len(noteworthies)})\n")
        for i, item in enumerate(noteworthies, 1):
            parts.append(_format_noteworthy(item, i))

    if glances:
        parts.append(f"## 👀 一眼扫 ({len(glances)})\n")
        for i, item in enumerate(glances, 1):
            parts.append(_format_glance(item, i))
        parts.append("")

    content = "\n".join(parts).rstrip() + "\n"
    target.write_text(content, encoding="utf-8")
    logger.info("Obsidian: wrote daily report → %s", target)
    return target


def _write_legacy_report(target: Path, date_str: str, data: dict[str, Any]) -> Path:
    """Render the pre-v2 (flat items + executive_summary) data shape."""
    items = data.get("items", []) or []
    exec_summary = (data.get("executive_summary") or "").strip()

    parts: list[str] = []
    parts.append(
        "---\n"
        f"date: {date_str}\n"
        "type: ai-daily-report\n"
        "format: legacy\n"
        f"items: {len(items)}\n"
        "tags:\n"
        "  - ai-daily\n"
        "  - rss-notion\n"
        "  - legacy\n"
        "---\n\n"
    )
    parts.append(f"# AI Daily — {date_str}\n")
    if exec_summary:
        parts.append("## 今日要点\n")
        parts.append(exec_summary + "\n")
    if items:
        parts.append(f"## 信息流 ({len(items)})\n")
        for i, it in enumerate(items, 1):
            title = it.get("title", "Untitled")
            url = it.get("url", "")
            source = it.get("source", "")
            summary = it.get("summary", "")
            score = it.get("interest_score") or it.get("score")
            tags = it.get("tags") or []
            line = f"### {i}. [{title}]({url})\n"
            line += f"*{source}*"
            if score is not None:
                line += f" · score {score}"
            if tags:
                line += f" · {', '.join(tags[:5])}"
            line += "\n"
            if summary:
                line += f"\n{summary}\n"
            parts.append(line)

    content = "\n".join(parts).rstrip() + "\n"
    target.write_text(content, encoding="utf-8")
    logger.info("Obsidian: wrote legacy report → %s", target)
    return target


def write_daily_report_obsidian_from_json(
    vault_root: str | Path,
    data_json_path: str | Path,
) -> Path:
    """Convenience: load data.json from disk and write the markdown."""
    data = json.loads(Path(data_json_path).read_text(encoding="utf-8"))
    return write_daily_report_obsidian(vault_root, data)


# ---------------------------------------------------------------------------
# Notion → Obsidian inbox migration
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff\-]+")


def _slugify(text: str, max_len: int = 60) -> str:
    """Make a filesystem-safe slug from a title (keeps CJK)."""
    text = text.strip().replace(" ", "-")
    text = _SLUG_RE.sub("-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def _rich_text_to_md(rt: list[dict]) -> str:
    """Render Notion rich_text array to inline markdown."""
    out: list[str] = []
    for span in rt or []:
        text = span.get("plain_text", "")
        if not text:
            text = span.get("text", {}).get("content", "")
        if not text:
            continue
        ann = span.get("annotations", {}) or {}
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = span.get("href")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _block_to_md(block: dict, indent: int = 0) -> str:
    """Convert a single Notion block to markdown. Handles nested children."""
    btype = block.get("type", "")
    data = block.get(btype, {}) or {}
    pad = "  " * indent

    if btype == "paragraph":
        return pad + _rich_text_to_md(data.get("rich_text", []))
    if btype == "heading_1":
        return f"# {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "heading_2":
        return f"## {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "heading_3":
        return f"### {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "bulleted_list_item":
        return f"{pad}- {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "numbered_list_item":
        return f"{pad}1. {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "quote":
        text = _rich_text_to_md(data.get("rich_text", []))
        return "\n".join(f"> {ln}" for ln in text.splitlines() or [""])
    if btype == "callout":
        text = _rich_text_to_md(data.get("rich_text", []))
        emoji = (data.get("icon") or {}).get("emoji", "💡")
        return f"> [!note] {emoji}\n" + "\n".join(f"> {ln}" for ln in text.splitlines() or [""])
    if btype == "code":
        text = _rich_text_to_md(data.get("rich_text", []))
        lang = data.get("language", "")
        return f"```{lang}\n{text}\n```"
    if btype == "divider":
        return "---"
    if btype == "to_do":
        check = "x" if data.get("checked") else " "
        return f"{pad}- [{check}] {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "toggle":
        return f"{pad}- {_rich_text_to_md(data.get('rich_text', []))}"
    if btype == "table":
        # Table rows are children — handled separately by caller
        return ""
    if btype == "table_row":
        cells = data.get("cells", []) or []
        rendered = [_rich_text_to_md(c) or " " for c in cells]
        return "| " + " | ".join(rendered) + " |"
    # Fallback: try generic rich_text
    rt = data.get("rich_text")
    if rt:
        return pad + _rich_text_to_md(rt)
    return ""


def _fetch_block_children(token: str, block_id: str) -> list[dict]:
    """Fetch all children of a block (paginated)."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        url = f"{NOTION_API}/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
            timeout=30.0,
        )
        r.raise_for_status()
        j = r.json()
        out.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
    return out


def _render_blocks(token: str, blocks: list[dict], indent: int = 0) -> str:
    """Render a list of Notion blocks to markdown, recursing into children."""
    lines: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        btype = b.get("type", "")
        if btype == "table":
            # Fetch table_row children separately and emit as markdown table
            rows = _fetch_block_children(token, b["id"])
            if rows:
                lines.append(_block_to_md(rows[0]))  # header
                # GFM separator
                cell_count = len((rows[0].get("table_row", {}) or {}).get("cells", []))
                lines.append("| " + " | ".join(["---"] * cell_count) + " |")
                for row in rows[1:]:
                    lines.append(_block_to_md(row))
                lines.append("")
            i += 1
            continue

        rendered = _block_to_md(b, indent)
        if rendered:
            lines.append(rendered)
        # Recurse into children if any
        if b.get("has_children"):
            children = _fetch_block_children(token, b["id"])
            child_md = _render_blocks(token, children, indent + 1)
            if child_md:
                lines.append(child_md)
        i += 1
    return "\n".join(lines)


def _extract_text(prop: dict) -> str:
    """Extract plain text from a Notion property of various types."""
    if not prop:
        return ""
    ptype = prop.get("type", "")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if ptype == "select":
        return (prop.get("select") or {}).get("name", "")
    if ptype == "multi_select":
        return ", ".join(o.get("name", "") for o in prop.get("multi_select", []))
    if ptype == "url":
        return prop.get("url", "") or ""
    if ptype == "date":
        d = prop.get("date") or {}
        return d.get("start", "") or ""
    if ptype == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    return ""


def migrate_inbox_from_notion(
    vault_root: str | Path,
    inbox_database_id: str = "d1da0a02-bb0f-4dfd-a7d0-8cf918e6f23c",
    notion_token: str | None = None,
) -> dict:
    """Pull all inbox pages from Notion and write each as a markdown file.

    Layout: ``<vault_root>/AI_Daily/Inbox/<YYYY-MM-DD>/<slug>.md``

    Returns a dict with ``written``, ``skipped``, ``errors`` counts.
    """
    token = notion_token or os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN not set in env")

    vault = Path(vault_root)
    inbox_dir = vault / "AI_Daily" / "Inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    # Paginate through all inbox pages
    all_pages: list[dict] = []
    cursor: str | None = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(
            f"{NOTION_API}/databases/{inbox_database_id}/query",
            headers=headers,
            json=body,
            timeout=60.0,
        )
        r.raise_for_status()
        j = r.json()
        all_pages.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")

    logger.info("Inbox migration: %d pages to convert", len(all_pages))

    written = skipped = errors = 0
    for p in all_pages:
        try:
            props = p.get("properties", {})
            title = _extract_text(props.get("名称")) or "Untitled"
            url = _extract_text(props.get("原文链接"))
            source = _extract_text(props.get("来源"))
            media = _extract_text(props.get("媒体来源"))
            importance = _extract_text(props.get("重要性"))
            collected = _extract_text(props.get("收录时间"))
            summary = _extract_text(props.get("摘要"))
            insight = _extract_text(props.get("洞察"))
            reason = _extract_text(props.get("入选理由"))
            categories = _extract_text(props.get("分类"))

            date_part = (collected or "1970-01-01")[:10]
            day_dir = inbox_dir / date_part
            day_dir.mkdir(parents=True, exist_ok=True)

            slug = _slugify(title)
            target = day_dir / f"{slug}.md"
            if target.exists():
                skipped += 1
                continue

            # Frontmatter
            fm: list[str] = ["---"]
            fm.append(f'title: "{_yaml_escape(title)}"')
            fm.append(f"date: {date_part}")
            fm.append("type: ai-inbox-item")
            if source:
                fm.append(f'source: "{_yaml_escape(source)}"')
            if media:
                fm.append(f'media: "{_yaml_escape(media)}"')
            if importance:
                fm.append(f'importance: "{_yaml_escape(importance)}"')
            if url:
                fm.append(f"url: {url}")
            if categories:
                fm.append(f'categories: "{_yaml_escape(categories)}"')
            fm.append("tags:")
            fm.append("  - ai-inbox")
            fm.append("---")

            body_parts: list[str] = []
            body_parts.append(f"# {title}")
            if url:
                body_parts.append(f"[原文链接]({url})")
            if reason:
                body_parts.append(f"**入选理由**: {reason}")
            if summary:
                lines = ["> [!quote] 摘要"] + [f"> {ln}" for ln in summary.splitlines()]
                body_parts.append("\n".join(lines))
            if insight:
                lines = ["> [!tip] 洞察"] + [f"> {ln}" for ln in insight.splitlines()]
                body_parts.append("\n".join(lines))

            # Page children blocks → markdown body
            # Skip paragraph blocks that are echo of properties
            # (notion_writer writes 📌 摘要 / 💡 洞察 / ✅ 入选理由 as page body too)
            children = _fetch_block_children(token, p["id"])
            ECHO_PREFIXES = ("📌", "💡", "✅")
            kept = []
            for b in children:
                if b.get("type") == "paragraph":
                    rt = (b.get("paragraph") or {}).get("rich_text", [])
                    text = "".join(t.get("plain_text", "") for t in rt).lstrip()
                    if text.startswith(ECHO_PREFIXES):
                        continue
                kept.append(b)
            if kept:
                blocks_md = _render_blocks(token, kept)
                if blocks_md.strip():
                    body_parts.append("---")
                    body_parts.append(blocks_md)

            content = "\n".join(fm) + "\n\n" + "\n\n".join(body_parts).strip() + "\n"
            target.write_text(content, encoding="utf-8")
            written += 1
            if written % 10 == 0:
                logger.info("  ... %d/%d", written + skipped, len(all_pages))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("Inbox item failed (%s): %s", p.get("id", "?"), exc)

    logger.info(
        "Inbox migration done: written=%d skipped=%d errors=%d", written, skipped, errors
    )
    return {"written": written, "skipped": skipped, "errors": errors, "total": len(all_pages)}
