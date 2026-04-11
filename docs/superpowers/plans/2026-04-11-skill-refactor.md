# RSS-Notion Pipeline → Claude Code Skill Refactor

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the RSS-Notion daily digest pipeline from a monolithic `python main.py` (which shells out to `claude -p` for LLM work) into a Claude Code Skill (`/daily-digest`) where Claude Code IS the LLM — eliminating all subprocess LLM calls while keeping the Python IO layer intact.

**Architecture:** The Skill orchestrates the pipeline by (1) calling Python scripts for pure IO (source fetching, Notion API, file writes), (2) performing LLM work directly in-conversation (scoring, report generation), and (3) using WebSearch for real-time market data. A new `pipeline_io.py` module exposes three CLI entry points: `fetch`, `write`, `maintain`. The existing `main.py` is preserved as fallback.

**Tech Stack:** Claude Code Skill (markdown), Python 3.12 (IO layer), Notion API (httpx), asyncio, WebSearch tool

**Rollback point:** `git reset --hard 07bd112`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `.claude/skills/daily-digest.md` | Main Skill definition — orchestrates full pipeline |
| `.claude/skills/market-watch.md` | Standalone market observation Skill |
| `pipeline_io.py` | CLI entry points for IO operations: `fetch`, `write`, `maintain` |

### Modified files
| File | Change |
|------|--------|
| `main.py` | No changes (preserved as fallback) |
| `generator/interest_scorer.py` | Extract prompt text to `prompts/scorer_system.txt` for Skill to reference |
| `generator/daily_report.py` | Extract prompt text to `prompts/report_system.txt` for Skill to reference |

### Files NOT modified (IO layer stays intact)
- `sources/*.py` — all fetchers unchanged
- `delivery/notion_writer.py` — all Notion helpers unchanged
- `delivery/obsidian_writer.py` — unchanged
- `config.json`, `sources.yaml` — unchanged

---

## Chunk 1: Pipeline IO Module

### Task 1: Create `pipeline_io.py` — fetch entry point

**Files:**
- Create: `pipeline_io.py`

This module wraps the existing source fetchers into a CLI-callable script that saves results to JSON. The Skill calls `python pipeline_io.py fetch` and reads the output file.

- [ ] **Step 1: Create `pipeline_io.py` with fetch command**

```python
"""CLI entry points for the RSS-Notion pipeline IO layer.

Usage:
    python pipeline_io.py fetch              # Phase 1: fetch all sources → output/{date}/sources.json
    python pipeline_io.py write <tiered.json> # Phase 4: write tiered JSON to Notion
    python pipeline_io.py maintain           # Phase 7-10: deep reader, clipper sync, cleanup
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    with open(Path(__file__).parent / "config.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# fetch: Phase 1 + 2 (source fetching + preference loading)
# ---------------------------------------------------------------------------

async def _fetch(config: dict, only_sources: list[str] | None = None) -> dict:
    """Fetch all sources and user preferences. Returns JSON-serializable dict."""
    import time
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
    from generator.interest_scorer import load_user_interests, load_clipper_items

    SOURCE_CLASSES = {
        "folo": FoloSource, "rss": RSSFetcher, "youtube": YouTubeSource,
        "hackernews": HackerNewsSource, "reddit": RedditSource,
        "arxiv": ArxivSource, "github_trending": GitHubTrendingSource,
        "xiaohongshu": XiaohongshuSource, "tavily": TavilySearchSource,
        "producthunt": ProductHuntSource,
    }

    pipeline_cfg = config.get("pipeline", {})
    sources_cfg = pipeline_cfg.get("sources", {})
    today = date.today().isoformat()
    t0 = time.time()

    # Build source instances
    instances = []
    for name, cls in SOURCE_CLASSES.items():
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", True):
            continue
        if only_sources and name not in only_sources:
            continue
        instances.append(cls(cfg))

    # Fetch concurrently
    results = await asyncio.gather(*[s.fetch() for s in instances])
    fetch_elapsed = time.time() - t0

    # Serialize items
    all_items = []
    source_stats = []
    for sr in results:
        for item in sr.items:
            all_items.append({
                "title": item.title or "",
                "url": item.url or "",
                "source_name": item.source_name or "",
                "description": (item.description or "")[:800],
                "author": item.author or "",
                "score": item.score,
                "published": item.published.isoformat() if item.published else None,
            })
        source_stats.append({
            "source_name": sr.source_name,
            "item_count": len(sr.items),
            "error": sr.error,
            "fetch_duration_ms": sr.fetch_duration_ms,
        })

    # Source stats text (for run report)
    stats_lines = [f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 抓取耗时: {fetch_elapsed:.1f}秒"]
    for s in source_stats:
        status = f"⚠️ {s['error']}" if s['error'] else "✅"
        stats_lines.append(f"【{s['source_name']}】{s['item_count']}条 | {s['fetch_duration_ms']}ms | {status}")

    # Load preferences
    clipper_text = await load_clipper_items(config)
    interests = await load_user_interests(config)
    interests_text = ""
    if interests:
        parts = []
        if interests.perspective:
            parts.append(f"视角: {interests.perspective}")
        if interests.topics:
            parts.append(f"关注话题: {', '.join(interests.topics)}")
        if interests.keywords:
            parts.append(f"关键词: {', '.join(interests.keywords)}")
        interests_text = "\n".join(parts)

    return {
        "date": today,
        "items": all_items,
        "source_stats": source_stats,
        "source_stats_text": "\n".join(stats_lines),
        "clipper_text": clipper_text or "",
        "interests_text": interests_text,
        "total_fetched": len(all_items),
    }


# ---------------------------------------------------------------------------
# write: Phase 4 (write tiered + report JSON to Notion)
# ---------------------------------------------------------------------------

async def _write(tiered_path: str, config: dict) -> dict:
    """Write scored/reported data to Notion. Returns summary dict."""
    from delivery.notion_writer import (
        write_scored_items_to_notion, write_daily_report_v2,
        write_run_report_to_notion, update_hub_page,
    )
    from generator.interest_scorer import ScoredItem
    from sources.models import SourceItem
    from sources.content_fetcher import fetch_content

    with open(tiered_path, encoding="utf-8") as f:
        data = json.load(f)

    tiered = data["tiered"]
    report_json = data.get("report")
    today = data["date"]
    total_fetched = data.get("total_fetched", 0)

    # Phase 3b: Enrich headline sources with full text
    logger.info("Enriching headline sources with full text...")
    enriched = 0
    for h in tiered.get("headline", []):
        url = h.get("best_source_url", "")
        if url and url.startswith("http"):
            content = await fetch_content(url, max_chars=3000, timeout=15)
            if content:
                h["best_source_content"] = content
                enriched += 1
    for n in tiered.get("noteworthy", []):
        url = n.get("best_source_url", "")
        if url and url.startswith("http"):
            content = await fetch_content(url, max_chars=2000, timeout=15)
            if content:
                n["best_source_content"] = content
                enriched += 1
    logger.info(f"  Enriched {enriched} sources")

    # Write daily report page
    report_url = await write_daily_report_v2(
        report_json or tiered, tiered, today, total_fetched=total_fetched,
    )
    logger.info(f"Daily report: {report_url}")

    # Update hub page
    hub_page_id = config.get("notion", {}).get("hub_page_id", "")
    if hub_page_id and report_json:
        one_liner = report_json.get("one_liner", tiered.get("daily_summary", "")).replace("**", "")
        headlines_text = " | ".join(h.get("event_title", "") for h in report_json.get("headline", []))
        hub_md = f"**{one_liner}**\n\n📰 {headlines_text}"
        await update_hub_page(hub_page_id, hub_md, report_url or "", today)

    # Write inbox items
    inbox_items = []
    for h in tiered.get("headline", []):
        rs = h.get("related_sources", [])
        ch = rs[0].get("channel", "一手/官方") if rs else "一手/官方"
        inbox_items.append(ScoredItem(
            original=SourceItem(title=h["event_title"], url=h.get("best_source_url", ""), source_name=h.get("best_source_name", "")),
            include=True, channel=ch, importance="高",
            what_happened=h.get("analysis", "")[:150], why_it_matters="",
            score_reason=f"头条 ({h.get('source_count', 1)}源)",
        ))
    for n in tiered.get("noteworthy", []):
        rs = n.get("related_sources", [])
        ch = rs[0].get("channel", "深度研究") if rs else "深度研究"
        inbox_items.append(ScoredItem(
            original=SourceItem(title=n["event_title"], url=n.get("best_source_url", ""), source_name=n.get("best_source_name", "")),
            include=True, channel=ch, importance="中",
            what_happened=n.get("summary", ""), why_it_matters=n.get("insight", ""),
            score_reason=f"关注 ({n.get('source_count', 1)}源)",
        ))
    for g in tiered.get("glance", []):
        inbox_items.append(ScoredItem(
            original=SourceItem(title=g.get("title", ""), url=g.get("url", ""), source_name=g.get("source_name", "")),
            include=True, channel=g.get("channel", "开源/技术/论文"), importance="低",
            what_happened=g.get("one_liner", ""), why_it_matters="", score_reason="速览",
        ))
    written = await write_scored_items_to_notion(inbox_items, today)
    logger.info(f"Wrote {written} items to inbox")

    # Write run report
    run_report = tiered.get("run_report", "")
    await write_run_report_to_notion(run_report, today)

    return {
        "report_url": report_url,
        "items_written": written,
        "enriched": enriched,
    }


# ---------------------------------------------------------------------------
# maintain: Phase 7-10 (deep reader, clipper sync, cleanup)
# ---------------------------------------------------------------------------

async def _maintain(config: dict) -> dict:
    """Run maintenance tasks: deep reader, clipper sync, Prism sync, cleanup."""
    from generator.deep_reader import process_deep_read_pages
    from delivery.notion_writer import cleanup_inbox, sync_clipper_items

    results = {}

    # Phase 7: Deep Reader
    logger.info("Deep Reader: processing pages...")
    deep_count = await process_deep_read_pages(config)
    results["deep_reader"] = deep_count or 0
    logger.info(f"  Deep Reader: {results['deep_reader']} pages")

    # Phase 8: Clipper sync
    logger.info("Clipper sync...")
    sync_result = await sync_clipper_items(config)
    results["clipper_sync"] = sync_result["processed"]
    logger.info(f"  Clipper: {results['clipper_sync']} processed")

    # Phase 9: Prism sync
    try:
        from scripts.sync_clipper_to_prism import fetch_clipper_items, fetch_article_text, ingest_to_prism, mark_as_processed
        logger.info("Prism sync...")
        clipper_items = await fetch_clipper_items(only_unprocessed=True)
        prism_synced = 0
        for item in clipper_items:
            if not item["url"]:
                continue
            text = await fetch_article_text(item["url"])
            if not text or len(text) < 100:
                text = item.get("summary", "")
            if text:
                r = ingest_to_prism(
                    title=item["title"], content=text, url=item["url"],
                    tags=item["tags"], summary=item.get("summary", ""),
                    insight=item.get("insight", ""), importance=item.get("importance", ""),
                )
                if r:
                    prism_synced += 1
            await mark_as_processed(item["page_id"])
        results["prism_sync"] = prism_synced
    except Exception as e:
        logger.warning(f"Prism sync skipped: {e}")
        results["prism_sync"] = 0

    # Phase 10: Cleanup
    logger.info("Inbox cleanup...")
    cleanup_stats = await cleanup_inbox(retention_days=7)
    results["cleanup_deleted"] = cleanup_stats["deleted"]
    logger.info(f"  Cleanup: {results['cleanup_deleted']} deleted")

    return results


# ---------------------------------------------------------------------------
# save: Save data.json + Obsidian markdown
# ---------------------------------------------------------------------------

async def _save(data: dict, config: dict) -> str:
    """Save data.json to output dir. Returns path."""
    today = data["date"]
    output_dir = config.get("pipeline", {}).get("pdf", {}).get("output_dir", "output")
    data_dir = Path(output_dir) / today
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Saved: {data_path}")
    return str(data_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pipeline IO layer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("fetch", help="Phase 1+2: fetch sources → sources.json")
    wp = sub.add_parser("write", help="Phase 4: write tiered.json to Notion")
    wp.add_argument("tiered_json", help="Path to tiered.json")
    sub.add_parser("maintain", help="Phase 7-10: deep reader, clipper, cleanup")

    args = parser.parse_args()
    config = _load_config()

    if args.command == "fetch":
        result = asyncio.run(_fetch(config))
        # Save to output/{date}/sources.json
        today = result["date"]
        out_dir = Path("output") / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "sources.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"SOURCES_JSON={out_path}")
        print(f"TOTAL_ITEMS={result['total_fetched']}")

    elif args.command == "write":
        result = asyncio.run(_write(args.tiered_json, config))
        print(f"REPORT_URL={result.get('report_url', '')}")
        print(f"ITEMS_WRITTEN={result.get('items_written', 0)}")

    elif args.command == "maintain":
        result = asyncio.run(_maintain(config))
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test fetch command**

Run: `cd D:\项目开发\RSS-Notion && python pipeline_io.py fetch`
Expected: outputs `SOURCES_JSON=output/2026-04-11/sources.json` and `TOTAL_ITEMS=~200`

- [ ] **Step 3: Commit**

```bash
git add pipeline_io.py
git commit -m "feat: add pipeline_io.py — CLI entry points for Skill IO layer"
```

---

## Chunk 2: Extract Prompt Templates

### Task 2: Extract LLM prompts to standalone files

The Skill needs to reference these prompts. Extract them from Python code to text files that the Skill definition can include via `Read`.

**Files:**
- Create: `prompts/scorer_system.txt` (from `interest_scorer.py` lines 461-511)
- Create: `prompts/report_system.txt` (from `daily_report.py` lines 21-118)

- [ ] **Step 1: Create scorer prompt file**

Extract the system prompt from `interest_scorer.py:461-511` into `prompts/scorer_system.txt`. This is the exact text between the triple-quoted string, verbatim — no modifications.

- [ ] **Step 2: Create report prompt file**

Extract the system prompt from `daily_report.py:21-118` into `prompts/report_system.txt`. Verbatim copy.

- [ ] **Step 3: Commit**

```bash
git add prompts/scorer_system.txt prompts/report_system.txt
git commit -m "chore: extract LLM prompts to prompts/ for Skill reference"
```

---

## Chunk 3: Main Skill Definition

### Task 3: Create `/daily-digest` Skill

**Files:**
- Create: `.claude/skills/daily-digest.md`

This is the core deliverable. The Skill definition tells Claude Code exactly how to run the pipeline step by step.

- [ ] **Step 1: Create the Skill file**

```markdown
---
name: daily-digest
description: Run the full AI daily digest pipeline — fetch sources, score/tier with LLM, generate report, write to Notion, append market observation. Invoke with /daily-digest.
---

# Daily Digest Pipeline

You are running the AI Daily Digest pipeline. Follow each phase sequentially. Do NOT skip phases unless the user explicitly says to.

## Phase 1: Fetch Sources

Run the Python IO layer to fetch all sources and user preferences:

\```bash
cd D:\项目开发\RSS-Notion && python pipeline_io.py fetch
\```

Parse the output to get `SOURCES_JSON` path and `TOTAL_ITEMS` count. Then read the sources JSON file.

## Phase 2: Score & Tier Items (YOU are the LLM)

Read the sources.json file from Phase 1. You will now perform the editorial scoring that was previously done by a `claude -p` subprocess.

Read the scoring prompt from `D:\项目开发\RSS-Notion\prompts\scorer_system.txt`.

Then apply that prompt to the items from sources.json:
- Use the `clipper_text` field as user interest signal
- If no clipper_text, use `interests_text`
- Include `source_stats_text` for the run report
- Process ALL items from the `items` array

Output your response as a **valid JSON object** with this exact schema:
```json
{
  "headline": [{"event_title": "...", "source_count": N, "best_source_url": "...", "best_source_name": "...", "analysis": "200-300字", "related_sources": [{"title": "...", "url": "...", "source_name": "...", "channel": "...", "one_liner": "..."}]}],
  "noteworthy": [{"event_title": "...", "source_count": N, "best_source_url": "...", "best_source_name": "...", "summary": "80-100字", "insight": "一句话", "related_sources": [...]}],
  "glance": [{"title": "...", "url": "...", "source_name": "...", "channel": "...", "one_liner": "..."}],
  "daily_summary": "50-100字",
  "run_report": "200-300字筛选反思",
  "events_total": N,
  "selected_total": N
}
```

Save this JSON to `output/{date}/tiered.json` using the Write tool.

## Phase 3: Generate Daily Report (YOU are the LLM)

Read the report prompt from `D:\项目开发\RSS-Notion\prompts\report_system.txt`.

Use the tiered JSON from Phase 2 as input, along with the original items from sources.json for additional context. Generate a polished report JSON with this schema:

```json
{
  "one_liner": "今日主线一句话",
  "headline": [...same structure with polished analysis...],
  "noteworthy": [...with priority field added...],
  "glance": [...],
  "signals": [{"keyword": "...", "note": "..."}]
}
```

Merge the report JSON with the tiered JSON and save to `output/{date}/tiered.json`:
```json
{
  "date": "YYYY-MM-DD",
  "tiered": {Phase 2 output},
  "report": {Phase 3 output},
  "total_fetched": N
}
```

## Phase 4: Write to Notion

Run the Python IO layer to write everything to Notion:

\```bash
cd D:\项目开发\RSS-Notion && python pipeline_io.py write output/{date}/tiered.json
\```

Parse the output for `REPORT_URL` — you'll need this for the market observation.

## Phase 5: Market Observation (WebSearch)

Use the WebSearch tool to get current stock prices for:
- **M7**: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA
- **Semis**: AVGO, SMH, SOXX
- **Indices**: SPY, QQQ

Then construct the market observation section:
1. Build a price table with ticker, price, change %
2. Write 3-4 "核心 read" insights connecting stock moves to today's headlines
3. Add a disclaimer callout

Append to Notion using:
\```bash
cd D:\项目开发\RSS-Notion && python -c "
import asyncio, json, sys
from dotenv import load_dotenv; load_dotenv()
from delivery.notion_writer import _heading2, _heading3, _paragraph, _callout_block, _plain_text, _bold_text, _divider, _table_block, _get_notion_client, _run_sync

PAGE_ID = sys.argv[1]
blocks_json = sys.argv[2]
blocks = json.loads(blocks_json)

async def push():
    client = _get_notion_client()
    await _run_sync(client.blocks.children.append, block_id=PAGE_ID, children=blocks)
    print('OK')
asyncio.run(push())
" NOTION_PAGE_ID 'BLOCKS_JSON'
\```

Also append markdown to Obsidian vault file `D:/研究空间/AI_Daily/{date}.md`.

## Phase 6: Maintenance

Run cleanup and sync tasks:

\```bash
cd D:\项目开发\RSS-Notion && python pipeline_io.py maintain
\```

## Phase 7: Summary

Print a summary:
- Total fetched → selected breakdown
- Report URL
- Market observation status
- Maintenance results
```

- [ ] **Step 2: Verify Skill is loadable**

Run: `/daily-digest` in Claude Code to verify the Skill loads.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/daily-digest.md
git commit -m "feat: add /daily-digest Skill — replaces claude-p with in-conversation LLM"
```

---

## Chunk 4: Market Watch Standalone Skill

### Task 4: Create `/market-watch` Skill

**Files:**
- Create: `.claude/skills/market-watch.md`

- [ ] **Step 1: Create the Skill file**

```markdown
---
name: market-watch
description: Fetch current stock prices via WebSearch and append market observation to today's Notion daily report + Obsidian. Use /market-watch.
---

# Market Watch

Append a 📈 市场观察 section to today's daily report.

## Step 1: Find today's report page

\```bash
cd D:\项目开发\RSS-Notion && grep -r "Daily report v2 created" output/$(date +%Y-%m-%d)/ 2>/dev/null || echo "Check Notion manually"
\```

Or ask the user for the Notion page URL/ID.

## Step 2: Fetch market data

Use WebSearch for current/latest closing prices:
- M7: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA
- Semis: AVGO, SMH/SOXX
- Indices: SPY, QQQ

## Step 3: Read today's headlines

Read `output/{date}/data.json` or `output/{date}/tiered.json` to get today's headline events.

## Step 4: Write market observation

Build 3-4 "核心 read" insights that connect stock price movements to today's AI/tech headlines. Each insight should:
- Lead with the price move (e.g., "GOOGL +3.9%")
- Connect it to a specific headline event
- Add your analysis of what it means

## Step 5: Append to Notion + Obsidian

Use the Notion block helpers via Python to append blocks to the report page.
Append markdown to `D:/研究空间/AI_Daily/{date}.md`.

Format: price table → 核心 read callouts → disclaimer callout.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/market-watch.md
git commit -m "feat: add /market-watch Skill — standalone market observation"
```

---

## Chunk 5: End-to-End Verification

### Task 5: Test the full Skill pipeline

- [ ] **Step 1: Run `/daily-digest` and verify output matches `python main.py`**

Compare:
1. Notion daily report page structure (headline/noteworthy/glance sections)
2. Notion inbox items (count, fields)
3. Obsidian markdown file content
4. data.json structure in output/

- [ ] **Step 2: Run `/market-watch` standalone**

Verify it correctly appends market observation without running the full pipeline.

- [ ] **Step 3: Verify `python main.py --skip-email` still works as fallback**

Run the old pipeline and confirm it produces the same output structure.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete Skill refactor — /daily-digest and /market-watch ready"
```

---

## Key Design Decisions

1. **`main.py` preserved as-is** — no changes, serves as fallback if Claude Code is unavailable
2. **Python IO layer unchanged** — all source fetchers, Notion writers, content enrichers stay in Python
3. **LLM work moves to Skill** — Claude Code does scoring + report generation in-conversation, eliminating `claude -p` subprocess and all Windows encoding issues
4. **Prompts extracted to files** — `prompts/scorer_system.txt` and `prompts/report_system.txt` are the single source of truth, referenced by both the Skill and `main.py`
5. **Market observation built into main Skill** — uses WebSearch tool, no separate API needed
6. **`pipeline_io.py` is the bridge** — thin CLI wrapper around existing async functions, designed for Skill to call via Bash
