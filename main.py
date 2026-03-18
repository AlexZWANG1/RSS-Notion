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
from sources.models import SourceResult, PipelineResult
from generator.summarizer import process_items_batch, generate_executive_summary
from generator.pdf_builder import build_pdf
from delivery.emailer import send_report_email

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
}


async def run_pipeline(
    config: dict,
    skip_email: bool = False,
    skip_notion: bool = False,
    only_sources: list[str] | None = None,
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

    # --- Phase: LLM processing ---
    logger.info("Phase 2: LLM processing...")
    model = llm_cfg.get("processing_model", "gpt-5.2")
    summary_model = llm_cfg.get("summary_model", "gpt-5.2")

    processed = await process_items_batch(all_items, model=model)
    result.processed_items = processed
    logger.info(f"Processed {len(processed)} items")

    # Executive summary
    logger.info("Generating executive summary...")
    summary = await generate_executive_summary(processed, model=summary_model)
    result.executive_summary = summary

    # --- Phase: PDF generation ---
    logger.info("Phase 3: Generating PDF...")
    output_dir = pdf_cfg.get("output_dir", "output")
    pdf_path = build_pdf(source_results, processed, summary, output_dir, today)
    result.pdf_path = pdf_path

    # Save data.json alongside PDF
    data_dir = Path(output_dir) / today
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "data.json"
    data_json = {
        "date": today,
        "executive_summary": summary,
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
            }
            for pi in processed
        ],
    }
    data_path.write_text(json.dumps(data_json, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Saved data.json: {data_path}")

    # --- Phase: Email ---
    if not skip_email:
        logger.info("Phase 4: Sending email...")
        result.email_sent = send_report_email(pdf_path, summary, today)
    else:
        logger.info("Phase 4: Email skipped (--skip-email)")

    # --- Done ---
    logger.info("=" * 50)
    logger.info(f"Pipeline complete! PDF: {pdf_path}")
    if result.errors:
        logger.warning(f"Errors: {result.errors}")
    logger.info("=" * 50)

    return result


def main():
    parser = argparse.ArgumentParser(description="AI Daily Digest Pipeline")
    parser.add_argument("--skip-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--skip-notion", action="store_true", help="Skip Folo/Notion source")
    parser.add_argument("--sources", type=str, help="Comma-separated source names (e.g. hackernews,arxiv)")
    args = parser.parse_args()

    only_sources = args.sources.split(",") if args.sources else None

    result = asyncio.run(run_pipeline(
        config=load_config(),
        skip_email=args.skip_email,
        skip_notion=args.skip_notion,
        only_sources=only_sources,
    ))

    # Exit 0 even with partial source errors (they're expected)
    sys.exit(0)


if __name__ == "__main__":
    main()
