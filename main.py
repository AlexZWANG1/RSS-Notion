"""AI Daily Digest — main pipeline entry point."""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
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
from sources.models import SourceResult, PipelineResult
from generator.interest_scorer import load_user_interests, load_clipper_items, score_items
from generator.daily_report import generate_daily_report
from generator.pdf_builder import build_pdf
from delivery.emailer import send_report_email
from delivery.notion_writer import (
    write_scored_items_to_notion,
    write_daily_report_v2,
    write_run_report_to_notion,
    cleanup_inbox,
    sync_clipper_items,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# Order matters: first sources get priority in dedup (_pre_filter keeps first seen)
SOURCE_CLASSES = {
    "folo": FoloSource,            # 最高优先级：用户订阅的 Twitter/博客/播客
    "rss": RSSFetcher,             # 补充 RSS（官方博客/投资机构）
    "youtube": YouTubeSource,      # YouTube 频道
    "hackernews": HackerNewsSource, # 补充源
    "reddit": RedditSource,
    "arxiv": ArxivSource,
    "github_trending": GitHubTrendingSource,
    "xiaohongshu": XiaohongshuSource,
    "tavily": TavilySearchSource,
    "producthunt": ProductHuntSource,
}


def _build_run_summary(tiered):
    """Return LLM-generated run report. No hardcoded content."""
    return tiered.get("run_report", "（LLM 未生成运行报告）")


def _build_email_body(all_items, selected, scored, source_results, summary, today):
    """Build the email body: digest overview + recommended reading + trends."""
    source_line = ", ".join(
        f"{sr.source_name}({len(sr.items)})" for sr in source_results if sr.items
    )

    # Group selected by importance
    high = [s for s in selected if s.importance == "高"]
    medium = [s for s in selected if s.importance != "高"]

    lines = [
        f"AI 认知日报 — {today}",
        f"{'='*40}",
        f"",
        f"今日 AI 扫描了 {len(all_items)} 条内容（来源: {source_line}），",
        f"经过 LLM 编辑筛选，推荐 {len(selected)} 条值得关注。",
        f"",
    ]

    if high:
        lines.append("⭐ 重点阅读（高重要性）")
        lines.append("-" * 30)
        for s in high:
            lines.append(f"  [{getattr(s, 'channel', '')}] {s.original.title}")
            lines.append(f"  → {getattr(s, 'what_happened', '')}")
            wm = getattr(s, "why_it_matters", "")
            if wm:
                lines.append(f"  💡 {wm}")
            lines.append(f"  🔗 {s.original.url}")
            lines.append("")

    if medium:
        lines.append("📌 值得一看")
        lines.append("-" * 30)
        for s in medium:
            lines.append(f"  [{getattr(s, 'channel', '')}] {s.original.title}")
            lines.append(f"  → {getattr(s, 'what_happened', '')}")
            lines.append(f"  🔗 {s.original.url}")
            lines.append("")

    lines.append("📈 核心趋势与发现")
    lines.append("=" * 40)
    lines.append(summary)
    lines.append("")
    lines.append("—— AI Daily Digest Pipeline 自动生成")

    return "\n".join(lines)


async def run_pipeline(
    config: dict,
    skip_email: bool = False,
    skip_notion: bool = False,
    only_sources: list[str] | None = None,
    interests_override: str | None = None,
) -> PipelineResult:
    """Run the full pipeline."""
    import time as _time
    pipeline_start = _time.time()

    today = date.today().isoformat()
    pipeline_cfg = config.get("pipeline", {})
    sources_cfg = pipeline_cfg.get("sources", {})
    llm_cfg = pipeline_cfg.get("llm", {})
    pdf_cfg = pipeline_cfg.get("pdf", {})

    result = PipelineResult(date=today)

    # --- Phase: Fetch sources ---
    logger.info("=" * 50)
    logger.info(f"Starting AI Daily Digest for {today}")
    logger.info("=" * 50)

    logger.info("Phase 1: Fetching data sources...")

    source_instances = []
    for name, cls in SOURCE_CLASSES.items():
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", True):
            continue
        if only_sources and name not in only_sources:
            continue
        if name == "folo" and skip_notion:
            continue
        source_instances.append(cls(cfg))

    # Fetch all sources concurrently
    fetch_tasks = [src.fetch() for src in source_instances]
    source_results: list[SourceResult] = await asyncio.gather(*fetch_tasks)
    result.sources = source_results

    # Collect all items
    all_items = []
    for sr in source_results:
        all_items.extend(sr.items)
        if sr.error:
            result.errors.append(f"{sr.source_name}: {sr.error}")

    logger.info(f"Fetched {len(all_items)} items from {len(source_results)} sources")

    if not all_items:
        logger.warning("No items fetched, generating minimal report")

    # --- Phase 2: Load preferences (Web Clipper + interests) ---
    logger.info("Phase 2: Loading preferences...")

    clipper_text = ""
    if not skip_notion:
        clipper_text = await load_clipper_items(config)
    logger.info(f"  Clipper signal: {'yes (' + str(clipper_text.count(chr(10)) + 1) + ' items)' if clipper_text else 'no'}")

    if interests_override:
        from generator.interest_scorer import UserInterests
        topics = [t.strip() for t in interests_override.split(",") if t.strip()]
        interests = UserInterests(perspective="AI/tech analyst", topics=topics, keywords=topics)
        logger.info(f"  Using CLI interests: {topics}")
    else:
        interests = await load_user_interests(config)
    logger.info(f"  Interests: {len(interests.topics)} topics")

    # --- Phase 3: Score items (tiered output) ---
    logger.info("Phase 3: Scoring items (tiered classification)...")

    # Build source stats text for LLM run report
    from datetime import datetime
    fetch_elapsed = _time.time() - pipeline_start
    source_stats_lines = [f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Phase 1 抓取耗时: {fetch_elapsed:.1f}秒"]
    for sr in source_results:
        status = f"⚠️ {sr.error}" if sr.error else "✅"
        source_stats_lines.append(f"【{sr.source_name}】{len(sr.items)}条 | {sr.fetch_duration_ms}ms | {status}")
    source_stats_text = "\n".join(source_stats_lines)

    tiered = await score_items(all_items, config, interests, clipper_text, source_stats_text)
    if not tiered:
        logger.error("Scoring failed, aborting pipeline")
        return result

    headline_count = len(tiered.get("headline", []))
    noteworthy_count = len(tiered.get("noteworthy", []))
    glance_count = len(tiered.get("glance", []))
    total_selected = headline_count + noteworthy_count + glance_count
    logger.info(f"  Result: {headline_count} headline, {noteworthy_count} noteworthy, {glance_count} glance")

    result.executive_summary = tiered.get("daily_summary", "")

    # --- Phase 4: Write to Notion ---
    if not skip_notion:
        logger.info("Phase 4: Writing to Notion...")

        # 4a: Call 2 — LLM generates structured JSON daily report
        logger.info("  4a: Generating daily report v2 (structured JSON)...")
        report_json = await generate_daily_report(tiered, all_items, config)

        # Write daily report page with native Notion blocks
        report_url = await write_daily_report_v2(
            report_json, tiered, today, total_fetched=len(all_items)
        )
        if report_url:
            logger.info(f"  Daily report v2: {report_url}")

        # Auto-update 信息流中心 page
        hub_page_id = config.get("notion", {}).get("hub_page_id", "")
        if hub_page_id:
            source = report_json if report_json else tiered
            # Strip any existing **bold** from one_liner to avoid nested bold
            one_liner = source.get("one_liner", source.get("daily_summary", ""))
            one_liner_clean = one_liner.replace("**", "")
            headlines_text = " | ".join(
                h.get("event_title", "") for h in source.get("headline", [])
            )
            hub_markdown = f"**{one_liner_clean}**\n\n📰 {headlines_text}"
            from delivery.notion_writer import update_hub_page
            await update_hub_page(hub_page_id, hub_markdown, report_url or "", today)
            logger.info("  Updated 信息流中心 page")

        # 4b: Write items to inbox — one entry per event (best source only, no duplicates)
        from generator.interest_scorer import ScoredItem
        from sources.models import SourceItem
        inbox_items = []
        for h in tiered.get("headline", []):
            # Pick best source's channel, fallback to first related_source
            rs = h.get("related_sources", [])
            ch = rs[0].get("channel", "一手/官方") if rs else "一手/官方"
            inbox_items.append(ScoredItem(
                original=SourceItem(title=h["event_title"], url=h.get("best_source_url", ""), source_name=h.get("best_source_name", "")),
                include=True, channel=ch, importance="高",
                what_happened=h.get("analysis", "")[:150],
                why_it_matters="", score_reason=f"头条 ({h.get('source_count', 1)}源)",
            ))
        for n in tiered.get("noteworthy", []):
            rs = n.get("related_sources", [])
            ch = rs[0].get("channel", "深度研究") if rs else "深度研究"
            inbox_items.append(ScoredItem(
                original=SourceItem(title=n["event_title"], url=n.get("best_source_url", ""), source_name=n.get("best_source_name", "")),
                include=True, channel=ch, importance="中",
                what_happened=n.get("summary", ""),
                why_it_matters=n.get("insight", ""), score_reason=f"关注 ({n.get('source_count', 1)}源)",
            ))
        for g in tiered.get("glance", []):
            inbox_items.append(ScoredItem(
                original=SourceItem(title=g.get("title", ""), url=g.get("url", ""), source_name=g.get("source_name", "")),
                include=True, channel=g.get("channel", "开源/技术/论文"), importance="低",
                what_happened=g.get("one_liner", ""),
                why_it_matters="", score_reason="速览",
            ))
        written = await write_scored_items_to_notion(inbox_items, today)
        logger.info(f"  Wrote {written} items to inbox")

        # 4c: Run report (100% LLM generated)
        run_summary = _build_run_summary(tiered)
        await write_run_report_to_notion(run_summary, today)
    else:
        logger.info("Phase 4: Notion write skipped (--skip-notion)")

    # --- Phase 5: Save data.json ---
    logger.info("Phase 5: Saving data...")
    output_dir = pdf_cfg.get("output_dir", "output")
    data_dir = Path(output_dir) / today
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "data.json"

    data_json = {
        "date": today,
        "daily_summary": tiered.get("daily_summary", ""),
        "tiered": tiered,
        "sources": [
            {
                "name": sr.source_name,
                "item_count": len(sr.items),
                "error": sr.error,
                "duration_ms": sr.fetch_duration_ms,
            }
            for sr in source_results
        ],
        "stats": {
            "total_fetched": len(all_items),
            "headline": headline_count,
            "noteworthy": noteworthy_count,
            "glance": glance_count,
        },
    }
    data_path.write_text(json.dumps(data_json, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"  Saved data.json: {data_path}")

    # --- Phase 6: Email ---
    if not skip_email:
        logger.info("Phase 6: Sending email...")
        # Build a simple email body from tiered output
        email_lines = [f"AI Daily Digest — {today}\n", tiered.get("daily_summary", ""), "\n📰 头条:"]
        for h in tiered.get("headline", []):
            email_lines.append(f"  • {h.get('event_title', '')} — {h.get('best_source_url', '')}")
        email_lines.append("\n🔍 值得关注:")
        for n in tiered.get("noteworthy", []):
            email_lines.append(f"  • {n.get('event_title', '')} — {n.get('summary', '')}")
        email_lines.append("\n⚡ 速览:")
        for g in tiered.get("glance", []):
            email_lines.append(f"  • {g.get('title', '')} — {g.get('one_liner', '')}")
        email_body = "\n".join(email_lines)

        image_path = str(data_dir / "report.png")
        attachment = image_path if Path(image_path).exists() else None
        result.email_sent = send_report_email(attachment, email_body, today)
    else:
        logger.info("Phase 6: Email skipped (--skip-email)")

    # --- Phase 7: Deep Reader (YouTube transcript summaries) ---
    if not skip_notion:
        logger.info("Phase 7: Deep Reader — processing 待深度阅读 pages...")
        from generator.deep_reader import process_deep_read_pages
        deep_count = await process_deep_read_pages(config)
        logger.info(f"  Deep Reader: {deep_count or 0} pages processed")

    # --- Phase 8: Web Clipper sync ---
    if not skip_notion:
        logger.info("Phase 8: Syncing Web Clipper items...")
        sync_result = await sync_clipper_items(config)
        logger.info(f"  Clipper sync: {sync_result['processed']} processed, {len(sync_result['errors'])} errors")

    # --- Phase 9: Inbox Cleanup ---
    if not skip_notion:
        logger.info("Phase 9: Cleaning up inbox...")
        cleanup_stats = await cleanup_inbox(retention_days=7)
        logger.info(f"  Cleanup: {cleanup_stats['deleted']} deleted")
    else:
        logger.info("Phase 9: Inbox cleanup skipped (--skip-notion)")

    # --- Done ---
    logger.info("=" * 50)
    logger.info(f"Pipeline complete!")
    logger.info(f"  Items: {len(all_items)} fetched → {total_selected} selected")
    if result.errors:
        logger.warning(f"Errors: {result.errors}")
    logger.info("=" * 50)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="AI Daily Digest — personalized AI/tech news agent",
        epilog="Examples:\n"
               "  python main.py                              # full run\n"
               "  python main.py --interests 'AI Agent, SaaS' # personalized\n"
               "  python main.py --skip-email --sources hackernews,arxiv\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--skip-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--skip-notion", action="store_true", help="Skip Notion read/write")
    parser.add_argument("--sources", type=str, help="Comma-separated source names (e.g. hackernews,arxiv)")
    parser.add_argument(
        "--interests", type=str,
        help="Comma-separated interest topics for personalized scoring "
             "(e.g. 'AI Agent, LLM inference, SaaS'). "
             "Overrides Notion config page.",
    )
    parser.add_argument(
        "--cleanup-only", action="store_true",
        help="Only run inbox cleanup (archive starred, delete expired), skip pipeline",
    )
    parser.add_argument(
        "--deep-read-only", action="store_true",
        help="Only run Deep Reader (process 待深度阅读 YouTube pages), skip pipeline",
    )
    args = parser.parse_args()

    if args.cleanup_only:
        stats = asyncio.run(cleanup_inbox(retention_days=7))
        print(f"Cleanup done: {stats['deleted']} deleted, {len(stats.get('errors', []))} errors")
        sys.exit(0)

    if args.deep_read_only:
        from generator.deep_reader import process_deep_read_pages
        count = asyncio.run(process_deep_read_pages(load_config()))
        print(f"Deep Reader done: {count} pages processed")
        sys.exit(0)

    only_sources = args.sources.split(",") if args.sources else None

    result = asyncio.run(run_pipeline(
        config=load_config(),
        skip_email=args.skip_email,
        skip_notion=args.skip_notion,
        only_sources=only_sources,
        interests_override=args.interests,
    ))

    # Exit 0 even with partial source errors (they're expected)
    sys.exit(0)


if __name__ == "__main__":
    main()
