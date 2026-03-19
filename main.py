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
from sources.models import SourceResult, PipelineResult
from generator.interest_scorer import load_user_interests, score_items, filter_items
from generator.summarizer import process_items_batch, generate_executive_summary
from generator.pdf_builder import build_pdf
from delivery.emailer import send_report_email
from delivery.notion_writer import (
    write_scored_items_to_notion,
    write_research_report_to_notion,
    write_run_report_to_notion,
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


SOURCE_CLASSES = {
    "hackernews": HackerNewsSource,
    "arxiv": ArxivSource,
    "reddit": RedditSource,
    "producthunt": ProductHuntSource,
    "github_trending": GitHubTrendingSource,
    "folo": FoloSource,
    "youtube": YouTubeSource,
}


def _build_run_summary(all_items, selected, scored, source_results, summary, threshold):
    """Build a rich run summary for Notion."""
    source_stats = "\n".join(
        f"  - {sr.source_name}: {len(sr.items)}条" + (f" (错误: {sr.error})" if sr.error else "")
        for sr in source_results
    )
    selected_list = "\n".join(
        f"  {i+1}. [{s.importance}] {s.original.title} — {s.one_line_summary}"
        for i, s in enumerate(selected)
    )
    topic_counts: dict[str, int] = {}
    for s in selected:
        topic_counts[s.topic] = topic_counts.get(s.topic, 0) + 1
    topic_dist = ", ".join(f"{t}({c})" for t, c in sorted(topic_counts.items(), key=lambda x: -x[1]))

    return (
        f"📊 处理统计\n"
        f"抓取 {len(all_items)} 条 → AI编辑筛选 → 入选 {len(selected)} 条\n\n"
        f"📡 数据源\n{source_stats}\n\n"
        f"🏷️ 话题分布: {topic_dist}\n\n"
        f"📋 入选内容\n{selected_list}\n\n"
        f"💡 核心发现\n{summary}"
    )


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
            lines.append(f"  [{s.topic}] {s.original.title}")
            lines.append(f"  → {s.one_line_summary}")
            if s.key_insight:
                lines.append(f"  💡 {s.key_insight}")
            lines.append(f"  🔗 {s.original.url}")
            lines.append("")

    if medium:
        lines.append("📌 值得一看")
        lines.append("-" * 30)
        for s in medium:
            lines.append(f"  [{s.topic}] {s.original.title}")
            lines.append(f"  → {s.one_line_summary}")
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

    # --- Phase 2: Load user interests from Notion config ---
    logger.info("Phase 2: Loading user interests from Notion...")
    schedule_cfg = config.get("schedule", {})
    threshold = schedule_cfg.get("relevance_threshold", 7)
    max_selected = schedule_cfg.get("max_selected", 15)

    if interests_override:
        # CLI --interests flag: quick personalization without Notion
        from generator.interest_scorer import UserInterests
        topics = [t.strip() for t in interests_override.split(",") if t.strip()]
        interests = UserInterests(
            perspective="AI/tech analyst",
            topics=topics,
            keywords=topics,  # use topics as keywords too
        )
        logger.info(f"  Using CLI interests: {topics}")
    else:
        interests = await load_user_interests(config)
    if interests.topics:
        logger.info(f"  Interests: {len(interests.topics)} topics, {len(interests.keywords)} keywords")
        if interests.designated_topic:
            logger.info(f"  Designated topic: {interests.designated_topic}")
    else:
        logger.info("  Using default interests")

    # --- Phase 3: Score items against user interests ---
    logger.info("Phase 3: Scoring items against user interests...")
    model = llm_cfg.get("processing_model", "gpt-5.2")
    summary_model = llm_cfg.get("summary_model", "gpt-5.2")

    scored = await score_items(all_items, interests, model=model)
    logger.info(f"  Scored {len(scored)} items")

    # Filter: LLM editorial decision (include=True), with score threshold as fallback
    selected = filter_items(scored, threshold=threshold, max_items=max_selected)
    included_count = sum(1 for s in scored if s.include)
    logger.info(f"  LLM included {included_count} items, final selected {len(selected)}")

    # Also do the old-style processing for PDF compatibility
    processed = await process_items_batch(all_items, model=model)
    result.processed_items = processed

    # Executive summary (use scored items for richer context)
    logger.info("Generating executive summary...")
    summary = await generate_executive_summary(processed, model=summary_model)
    result.executive_summary = summary

    # --- Phase 4: Write to Notion ---
    if not skip_notion:
        logger.info("Phase 4: Writing to Notion...")

        # Write selected items to inbox
        written = await write_scored_items_to_notion(selected, today)
        logger.info(f"  Wrote {written} items to Notion inbox")

        # Write run report (enriched with editorial details)
        run_summary = _build_run_summary(
            all_items, selected, scored, source_results, summary, threshold
        )
        await write_run_report_to_notion(run_summary, today)
    else:
        logger.info("Phase 4: Notion write-back skipped (--skip-notion)")

    # --- Phase 5: PDF generation ---
    logger.info("Phase 5: Generating PDF...")
    output_dir = pdf_cfg.get("output_dir", "output")
    pdf_path = build_pdf(source_results, processed, summary, output_dir, today)
    result.pdf_path = pdf_path

    # Save data.json alongside PDF (enriched with scores)
    data_dir = Path(output_dir) / today
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "data.json"

    # Build scored lookup for enriching data.json
    score_map = {s.original.url: s for s in scored}

    data_json = {
        "date": today,
        "executive_summary": summary,
        "interests": {
            "topics": interests.topics,
            "keywords": interests.keywords[:20],
            "designated_topic": interests.designated_topic,
        },
        "sources": [
            {
                "name": sr.source_name,
                "item_count": len(sr.items),
                "error": sr.error,
                "duration_ms": sr.fetch_duration_ms,
            }
            for sr in source_results
        ],
        "items": [
            {
                "title": pi.original.title,
                "url": pi.original.url,
                "source": pi.original.source_name,
                "summary": pi.one_line_summary,
                "category": pi.category,
                "relevance": pi.relevance,
                "tags": pi.tags,
                "score": pi.original.score,
                "interest_score": score_map[pi.original.url].score if pi.original.url in score_map else None,
                "topic": score_map[pi.original.url].topic if pi.original.url in score_map else None,
                "content_type": score_map[pi.original.url].content_type if pi.original.url in score_map else None,
                "source_category": score_map[pi.original.url].source_category if pi.original.url in score_map else None,
            }
            for pi in processed
        ],
    }
    data_path.write_text(json.dumps(data_json, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Saved data.json: {data_path}")

    # --- Phase 6: Email (send PNG image + rich digest body) ---
    if not skip_email:
        logger.info("Phase 6: Sending email...")
        image_path = str(Path(output_dir) / today / "report.png")
        attachment = image_path if Path(image_path).exists() else pdf_path
        email_body = _build_email_body(
            all_items, selected, scored, source_results, summary, today
        )
        result.email_sent = send_report_email(attachment, email_body, today)
    else:
        logger.info("Phase 6: Email skipped (--skip-email)")

    # --- Done ---
    logger.info("=" * 50)
    logger.info(f"Pipeline complete! PDF: {pdf_path}")
    logger.info(f"  Items: {len(all_items)} fetched → {len(selected)} selected")
    if not skip_notion:
        logger.info(f"  Notion: {len(selected)} items written to inbox")
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
    args = parser.parse_args()

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
