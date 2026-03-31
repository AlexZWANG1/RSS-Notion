"""Sync Web Clipper articles from Notion → Prism knowledge base.

Reads articles from the 🔖 Web Clipper 收集 database in Notion,
fetches full text via Jina Reader, and ingests them into Prism/IRIS's
knowledge base (SQLite + vector embeddings) for investment research retrieval.

Usage:
    python scripts/sync_clipper_to_prism.py              # sync all unprocessed
    python scripts/sync_clipper_to_prism.py --force       # re-sync all

Requires:
    - NOTION_TOKEN in .env
    - Prism project at D:/项目开发/二级投研自动化/iris/
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

# Add both projects to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRISM_ROOT = Path("D:/项目开发/二级投研自动化/iris")

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_ROOT))

from dotenv import load_dotenv
# Load RSS-Notion .env first (for NOTION_TOKEN)
load_dotenv(PROJECT_ROOT / ".env")
# Then load Prism .env to override embedding config (EMBEDDING_PROVIDER=ollama)
load_dotenv(PRISM_ROOT / ".env", override=True)
# Ensure NOTION_TOKEN survives the override
load_dotenv(PROJECT_ROOT / ".env", override=False)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Notion config
CLIPPER_DB_ID = "148f50ee-e7b6-4cdd-9255-979f4d9e2855"


async def fetch_clipper_items(only_unprocessed: bool = True) -> list[dict]:
    """Fetch articles from Notion Web Clipper database."""
    import httpx

    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        logger.error("NOTION_TOKEN not set")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    filters = {}
    if only_unprocessed:
        filters = {
            "filter": {
                "property": "已处理",
                "checkbox": {"equals": False},
            },
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.notion.com/v1/databases/{CLIPPER_DB_ID}/query",
            headers=headers,
            json={**filters, "sorts": [{"property": "摘取时间", "direction": "descending"}]},
        )
        resp.raise_for_status()
        pages = resp.json().get("results", [])

    items = []
    for page in pages:
        props = page.get("properties", {})

        title_parts = props.get("标题", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)

        url = props.get("userDefined:URL", {}).get("url", "") or ""

        tags_raw = props.get("标签", {}).get("multi_select", [])
        tags = [t.get("name", "") for t in tags_raw]

        summary_parts = props.get("摘要", {}).get("rich_text", [])
        summary = "".join(t.get("plain_text", "") for t in summary_parts)

        insight_parts = props.get("洞察", {}).get("rich_text", [])
        insight = "".join(t.get("plain_text", "") for t in insight_parts)

        importance = props.get("重要性", {}).get("select", {})
        importance_name = importance.get("name", "") if importance else ""

        items.append({
            "page_id": page["id"],
            "title": title,
            "url": url,
            "tags": tags,
            "summary": summary,
            "insight": insight,
            "importance": importance_name,
        })

    return items


async def fetch_article_text(url: str) -> str:
    """Fetch full article text via Jina Reader."""
    from sources.content_fetcher import fetch_content
    text = await fetch_content(url, max_chars=30000, timeout=30)
    return text or ""


async def mark_as_processed(page_id: str):
    """Mark a Notion page as processed."""
    import httpx

    token = os.environ.get("NOTION_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=headers,
            json={"properties": {"已处理": {"checkbox": True}}},
        )


def ingest_to_prism(
    title: str,
    content: str,
    url: str,
    tags: list[str],
    summary: str = "",
    insight: str = "",
    importance: str = "",
) -> dict | None:
    """Ingest a document into Prism's knowledge base."""
    try:
        from tools.retrieval import SQLiteRetriever

        db_path = str(PRISM_ROOT / "iris.db")
        retriever = SQLiteRetriever(db_path=db_path)

        # Build enriched content: article text + existing summary/insight
        enriched_parts = []
        if summary:
            enriched_parts.append(f"摘要: {summary}")
        if insight:
            enriched_parts.append(f"洞察: {insight}")
        enriched_parts.append(content)
        full_content = "\n\n".join(enriched_parts)

        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16] if url else None

        # Check if already exists
        with retriever._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM knowledge_documents WHERE url_hash = ?",
                (url_hash,),
            ).fetchone()
            if existing:
                logger.info(f"  Already in Prism: {title[:50]}")
                return None

        result = retriever.save_document(
            title=title,
            doc_type="url_content",
            content_text=full_content,
            source_type="web_clipper",
            source_name="Notion Web Clipper",
            tags=tags + ([f"importance:{importance}"] if importance else []),
            canonical_url=url,
            url_hash=url_hash,
            category="research",
        )
        return result

    except Exception as e:
        logger.error(f"  Prism ingest failed: {e}")
        return None


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync Web Clipper → Prism knowledge base")
    parser.add_argument("--force", action="store_true", help="Re-sync all items (ignore 已处理)")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("Web Clipper → Prism Knowledge Base Sync")
    logger.info("=" * 50)

    # Fetch clipper items
    items = await fetch_clipper_items(only_unprocessed=not args.force)
    logger.info(f"Found {len(items)} items to sync")

    if not items:
        logger.info("Nothing to sync")
        return

    synced = 0
    for item in items:
        title = item["title"]
        url = item["url"]
        logger.info(f"Processing: {title[:60]}")

        if not url:
            logger.warning(f"  No URL, skipping")
            continue

        # Fetch full text
        text = await fetch_article_text(url)
        if not text or len(text) < 100:
            logger.warning(f"  Could not fetch content ({len(text)} chars)")
            # Still ingest with summary if available
            if item["summary"]:
                text = item["summary"]
            else:
                await mark_as_processed(item["page_id"])
                continue

        # Ingest to Prism
        result = ingest_to_prism(
            title=title,
            content=text,
            url=url,
            tags=item["tags"],
            summary=item["summary"],
            insight=item["insight"],
            importance=item["importance"],
        )

        if result:
            logger.info(f"  ✅ Ingested: {result.get('id', '')} ({result.get('chunks_count', 0)} chunks)")
            synced += 1

        # Mark as processed in Notion
        await mark_as_processed(item["page_id"])

    logger.info("=" * 50)
    logger.info(f"Done: {synced}/{len(items)} items synced to Prism")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
