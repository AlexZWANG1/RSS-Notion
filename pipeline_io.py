"""pipeline_io.py — CLI bridge for Claude Code Skill to call pipeline IO.

Commands:
    python pipeline_io.py fetch           Fetch all sources + load prefs → sources.json
    python pipeline_io.py write <file>    Read tiered JSON, enrich, write to Notion
    python pipeline_io.py maintain        Deep reader, clipper sync, prism sync, cleanup
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sources.hackernews import HackerNewsSource
from sources.arxiv_source import ArxivSource
from sources.reddit import RedditSource
from sources.producthunt import ProductHuntSource
from sources.github_trending import GitHubTrendingSource
from sources.folo import FoloSource
from sources.youtube import YouTubeSource
from sources.xiaohongshu import XiaohongshuSource
from sources.rss_fetcher import RSSFetcher
from sources.tavily_search import TavilySearchSource
from sources.models import SourceResult, SourceItem
from generator.interest_scorer import (
    load_user_interests,
    load_clipper_items,
    _pre_filter,
    ScoredItem,
    UserInterests,
)

# Logging → stderr only
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

SOURCE_CLASSES = {
    "folo": FoloSource,
    "rss": RSSFetcher,
    "youtube": YouTubeSource,
    "hackernews": HackerNewsSource,
    "reddit": RedditSource,
    "arxiv": ArxivSource,
    "github_trending": GitHubTrendingSource,
    "xiaohongshu": XiaohongshuSource,
    "tavily": TavilySearchSource,
    "producthunt": ProductHuntSource,
}


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def _serialize_datetime(obj):
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _source_item_to_dict(item: SourceItem) -> dict:
    """Convert SourceItem to JSON-safe dict."""
    return {
        "title": item.title,
        "url": item.url,
        "source_name": item.source_name,
        "description": item.description,
        "author": item.author,
        "score": item.score,
        "published": item.published.isoformat() if item.published else None,
        "extra": item.extra,
    }


def _dict_to_source_item(d: dict) -> SourceItem:
    """Reconstruct SourceItem from dict."""
    published = None
    if d.get("published"):
        try:
            published = datetime.fromisoformat(d["published"])
        except (ValueError, TypeError):
            pass
    return SourceItem(
        title=d.get("title", ""),
        url=d.get("url", ""),
        source_name=d.get("source_name", ""),
        description=d.get("description", ""),
        author=d.get("author", ""),
        score=d.get("score"),
        published=published,
        extra=d.get("extra", {}),
    )


# =========================================================================
# FETCH command
# =========================================================================

async def cmd_fetch() -> None:
    """Phase 1+2: fetch sources concurrently + load user preferences."""
    config = load_config()
    today = date.today().isoformat()
    pipeline_cfg = config.get("pipeline", {})
    sources_cfg = pipeline_cfg.get("sources", {})

    # Phase 1: build source instances
    logger.info("Phase 1: Fetching data sources...")
    source_instances = []
    for name, cls in SOURCE_CLASSES.items():
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", True):
            continue
        source_instances.append(cls(cfg))

    # Fetch all sources concurrently
    fetch_tasks = [src.fetch() for src in source_instances]
    source_results: list[SourceResult] = await asyncio.gather(*fetch_tasks)

    all_items = []
    source_summaries = []
    for sr in source_results:
        all_items.extend(sr.items)
        source_summaries.append({
            "name": sr.source_name,
            "item_count": len(sr.items),
            "error": sr.error,
            "duration_ms": sr.fetch_duration_ms,
        })
        if sr.error:
            logger.warning("  %s: %s", sr.source_name, sr.error)
        else:
            logger.info("  %s: %d items (%dms)", sr.source_name, len(sr.items), sr.fetch_duration_ms)

    logger.info("Total fetched: %d items from %d sources", len(all_items), len(source_results))

    # Phase 2: load preferences
    logger.info("Phase 2: Loading preferences...")
    clipper_text = await load_clipper_items(config)
    interests = await load_user_interests(config)
    logger.info("  Interests: %d topics, clipper: %s", len(interests.topics), "yes" if clipper_text else "no")

    # Pre-filter (dedup + cap)
    filtered = _pre_filter(all_items)
    logger.info("After pre-filter: %d items", len(filtered))

    # Build source stats text for later use in scoring
    source_stats_lines = []
    for sr in source_results:
        status = f"error: {sr.error}" if sr.error else "ok"
        source_stats_lines.append(f"{sr.source_name}: {len(sr.items)} items, {sr.fetch_duration_ms}ms, {status}")
    source_stats_text = "\n".join(source_stats_lines)

    # Save to output/{date}/sources.json
    output_dir = Path("output") / today
    output_dir.mkdir(parents=True, exist_ok=True)
    sources_path = output_dir / "sources.json"

    payload = {
        "date": today,
        "total_items": len(filtered),
        "total_raw": len(all_items),
        "sources": source_summaries,
        "source_stats_text": source_stats_text,
        "interests": {
            "perspective": interests.perspective,
            "topics": interests.topics,
            "keywords": interests.keywords,
            "designated_topic": interests.designated_topic,
            "research_titles": interests.research_titles,
        },
        "clipper_text": clipper_text,
        "items": [_source_item_to_dict(item) for item in filtered],
    }

    sources_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_serialize_datetime),
        encoding="utf-8",
    )
    logger.info("Saved: %s", sources_path)

    # Structured output to stdout for Skill parsing
    print(f"SOURCES_JSON={sources_path}")
    print(f"TOTAL_ITEMS={len(filtered)}")


# =========================================================================
# WRITE command
# =========================================================================

async def cmd_write(tiered_path: str) -> None:
    """Phase 4: read tiered JSON, enrich headlines, write to Notion."""
    config = load_config()

    # Read tiered input
    tiered_data = json.loads(Path(tiered_path).read_text(encoding="utf-8"))
    today = tiered_data.get("date", date.today().isoformat())
    tiered = tiered_data.get("tiered", tiered_data)
    report = tiered_data.get("report")  # optional Call 2 output
    total_fetched = tiered_data.get("total_fetched", 0)

    # Phase 3b: Enrich headline/noteworthy with full text
    logger.info("Enriching headline sources with full text...")
    from sources.content_fetcher import fetch_content

    enriched_count = 0
    for h in tiered.get("headline", []):
        url = h.get("best_source_url", "")
        if url and url.startswith("http"):
            content = await fetch_content(url, max_chars=3000, timeout=15)
            if content:
                h["best_source_content"] = content
                enriched_count += 1
    for n in tiered.get("noteworthy", []):
        url = n.get("best_source_url", "")
        if url and url.startswith("http"):
            content = await fetch_content(url, max_chars=2000, timeout=15)
            if content:
                n["best_source_content"] = content
                enriched_count += 1
    logger.info("Enriched %d sources with full text", enriched_count)

    # 4a: Write daily report page
    logger.info("Writing daily report to Notion...")
    from delivery.notion_writer import (
        write_scored_items_to_notion,
        write_daily_report_v2,
        write_run_report_to_notion,
        update_hub_page,
    )

    report_url = await write_daily_report_v2(
        report, tiered, today, total_fetched=total_fetched,
    )
    logger.info("Daily report: %s", report_url or "failed")

    # Extract page_id from URL (format: https://www.notion.so/...-<uuid_no_dashes>)
    report_page_id = ""
    if report_url:
        # Notion URLs end with a 32-char hex ID (UUID without dashes)
        url_parts = report_url.rstrip("/").split("-")
        if url_parts:
            candidate = url_parts[-1]
            if len(candidate) == 32:
                report_page_id = f"{candidate[:8]}-{candidate[8:12]}-{candidate[12:16]}-{candidate[16:20]}-{candidate[20:]}"

    # 4a+: Update hub page
    hub_page_id = config.get("notion", {}).get("hub_page_id", "")
    if hub_page_id:
        source = report if report else tiered
        one_liner = source.get("one_liner", source.get("daily_summary", ""))
        one_liner_clean = one_liner.replace("**", "")
        headlines_text = " | ".join(
            h.get("event_title", "") for h in source.get("headline", [])
        )
        hub_markdown = f"**{one_liner_clean}**\n\n\U0001f4f0 {headlines_text}"
        await update_hub_page(hub_page_id, hub_markdown, report_url or "", today)
        logger.info("Updated hub page")

    # 4b: Write inbox items
    logger.info("Writing inbox items...")
    inbox_items = []
    for h in tiered.get("headline", []):
        rs = h.get("related_sources", [])
        ch = rs[0].get("channel", "一手/官方") if rs else "一手/官方"
        inbox_items.append(ScoredItem(
            original=SourceItem(
                title=h["event_title"],
                url=h.get("best_source_url", ""),
                source_name=h.get("best_source_name", ""),
            ),
            include=True, channel=ch, importance="高",
            what_happened=h.get("analysis", "")[:150],
            why_it_matters="",
            score_reason=f"头条 ({h.get('source_count', 1)}源)",
        ))
    for n in tiered.get("noteworthy", []):
        rs = n.get("related_sources", [])
        ch = rs[0].get("channel", "深度研究") if rs else "深度研究"
        inbox_items.append(ScoredItem(
            original=SourceItem(
                title=n["event_title"],
                url=n.get("best_source_url", ""),
                source_name=n.get("best_source_name", ""),
            ),
            include=True, channel=ch, importance="中",
            what_happened=n.get("summary", ""),
            why_it_matters=n.get("insight", ""),
            score_reason=f"关注 ({n.get('source_count', 1)}源)",
        ))
    for g in tiered.get("glance", []):
        inbox_items.append(ScoredItem(
            original=SourceItem(
                title=g.get("title", ""),
                url=g.get("url", ""),
                source_name=g.get("source_name", ""),
            ),
            include=True,
            channel=g.get("channel", "开源/技术/论文"),
            importance="低",
            what_happened=g.get("one_liner", ""),
            why_it_matters="",
            score_reason="速览",
        ))

    written = await write_scored_items_to_notion(inbox_items, today)
    logger.info("Wrote %d items to inbox", written)

    # 4c: Run report
    run_summary = tiered.get("run_report", "（LLM 未生成运行报告）")
    await write_run_report_to_notion(run_summary, today)

    # Save data.json
    output_dir = Path("output") / today
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "data.json"

    headline_count = len(tiered.get("headline", []))
    noteworthy_count = len(tiered.get("noteworthy", []))
    glance_count = len(tiered.get("glance", []))

    data_json = {
        "date": today,
        "daily_summary": tiered.get("daily_summary", ""),
        "tiered": tiered,
        "sources": tiered_data.get("sources", []),
        "stats": {
            "total_fetched": total_fetched,
            "headline": headline_count,
            "noteworthy": noteworthy_count,
            "glance": glance_count,
        },
    }
    data_path.write_text(
        json.dumps(data_json, ensure_ascii=False, indent=2, default=_serialize_datetime),
        encoding="utf-8",
    )
    logger.info("Saved: %s", data_path)

    # Structured output to stdout
    print(f"REPORT_URL={report_url or ''}")
    print(f"REPORT_PAGE_ID={report_page_id}")
    print(f"ITEMS_WRITTEN={written}")


# =========================================================================
# MAINTAIN command
# =========================================================================

async def cmd_maintain() -> None:
    """Phase 7-10: deep reader, clipper sync, prism sync, inbox cleanup."""
    config = load_config()
    results = {}

    # Phase 7: Deep Reader
    logger.info("Phase 7: Deep Reader...")
    try:
        from generator.deep_reader import process_deep_read_pages
        deep_count = await process_deep_read_pages(config)
        results["deep_reader"] = {"pages_processed": deep_count or 0}
        logger.info("  Deep Reader: %d pages", deep_count or 0)
    except Exception as e:
        results["deep_reader"] = {"error": str(e)}
        logger.warning("  Deep Reader failed: %s", e)

    # Phase 8: Web Clipper sync
    logger.info("Phase 8: Clipper sync...")
    try:
        from delivery.notion_writer import sync_clipper_items
        sync_result = await sync_clipper_items(config)
        results["clipper_sync"] = {
            "processed": sync_result.get("processed", 0),
            "errors": len(sync_result.get("errors", [])),
        }
        logger.info("  Clipper sync: %d processed", sync_result.get("processed", 0))
    except Exception as e:
        results["clipper_sync"] = {"error": str(e)}
        logger.warning("  Clipper sync failed: %s", e)

    # Phase 9: Prism sync
    logger.info("Phase 9: Prism sync...")
    try:
        from scripts.sync_clipper_to_prism import (
            fetch_clipper_items,
            fetch_article_text,
            ingest_to_prism,
            mark_as_processed,
        )
        clipper_items = await fetch_clipper_items(only_unprocessed=True)
        prism_synced = 0
        for item in clipper_items:
            if not item.get("url"):
                continue
            text = await fetch_article_text(item["url"])
            if not text or len(text) < 100:
                text = item.get("summary", "")
            if text:
                r = ingest_to_prism(
                    title=item["title"],
                    content=text,
                    url=item["url"],
                    tags=item["tags"],
                    summary=item.get("summary", ""),
                    insight=item.get("insight", ""),
                    importance=item.get("importance", ""),
                )
                if r:
                    prism_synced += 1
            await mark_as_processed(item["page_id"])
        results["prism_sync"] = {
            "total": len(clipper_items),
            "ingested": prism_synced,
        }
        logger.info("  Prism sync: %d/%d", prism_synced, len(clipper_items))
    except Exception as e:
        results["prism_sync"] = {"error": str(e)}
        logger.warning("  Prism sync skipped: %s", e)

    # Phase 10: Inbox cleanup
    logger.info("Phase 10: Inbox cleanup...")
    try:
        from delivery.notion_writer import cleanup_inbox
        cleanup_stats = await cleanup_inbox(retention_days=7)
        results["cleanup"] = {"deleted": cleanup_stats.get("deleted", 0)}
        logger.info("  Cleanup: %d deleted", cleanup_stats.get("deleted", 0))
    except Exception as e:
        results["cleanup"] = {"error": str(e)}
        logger.warning("  Cleanup failed: %s", e)

    # JSON summary to stdout
    print(json.dumps(results, ensure_ascii=False))


# =========================================================================
# CLI entry point
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Pipeline IO bridge for Claude Code Skill",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="Phase 1+2: fetch sources + load preferences")

    write_parser = sub.add_parser("write", help="Phase 4: write tiered data to Notion")
    write_parser.add_argument("tiered_json", help="Path to tiered JSON file")

    sub.add_parser("maintain", help="Phase 7-10: deep reader, sync, cleanup")

    args = parser.parse_args()

    if args.command == "fetch":
        asyncio.run(cmd_fetch())
    elif args.command == "write":
        asyncio.run(cmd_write(args.tiered_json))
    elif args.command == "maintain":
        asyncio.run(cmd_maintain())


if __name__ == "__main__":
    main()
