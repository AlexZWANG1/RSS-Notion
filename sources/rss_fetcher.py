"""Generic RSS feed fetcher — reads feeds from sources.yaml."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import aiohttp
import feedparser
import yaml

from sources.base import BaseSource
from sources.content_fetcher import fetch_content_batch
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_SOURCES_YAML = Path(__file__).resolve().parent.parent / "sources.yaml"


class RSSFetcher(BaseSource):
    """Fetch items from multiple RSS feeds defined in sources.yaml."""

    name = "RSS订阅"
    icon = "📡"

    def __init__(self, config: dict):
        super().__init__(config)
        self.max_age_days = config.get("max_age_days", 3)
        self.feeds = self._load_feeds()

    def _load_feeds(self) -> list[dict]:
        """Load the rss feed list from sources.yaml."""
        try:
            with open(_SOURCES_YAML, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("rss", [])
        except Exception as e:
            logger.error(f"Failed to load sources.yaml: {e}")
            return []

    async def _fetch(self) -> list[SourceItem]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        tasks = [self._fetch_feed(feed, cutoff) for feed in self.feeds]
        results = await asyncio.gather(*tasks)
        # Flatten list of lists
        items: list[SourceItem] = []
        for feed_items in results:
            items.extend(feed_items)
        # Sort by published date descending (newest first)
        items.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        # Enrich items with full text via Jina Reader
        items = await self._enrich_items(items)
        return items

    @staticmethod
    async def _enrich_items(items: list[SourceItem]) -> list[SourceItem]:
        """Enrich items with short descriptions via Jina Reader full text."""
        urls_to_fetch: list[str] = []
        indices: list[int] = []
        for i, item in enumerate(items):
            # Skip items that already have substantial content
            if len(item.description or "") >= 200:
                continue
            if not (item.url or "").startswith("http"):
                continue
            urls_to_fetch.append(item.url)
            indices.append(i)

        if urls_to_fetch:
            logger.info(f"[RSS] Enriching {len(urls_to_fetch)} items with Jina Reader")
            bodies = await fetch_content_batch(urls_to_fetch)
            enriched = 0
            for idx, body in zip(indices, bodies):
                if body and len(body) > len(items[idx].description or ""):
                    items[idx].description = body
                    enriched += 1
            logger.info(f"[RSS] Enriched {enriched}/{len(urls_to_fetch)} items")

        return items

    async def _fetch_feed(self, feed_cfg: dict, cutoff: datetime) -> list[SourceItem]:
        """Fetch and parse a single RSS feed."""
        feed_name = feed_cfg.get("name", "Unknown")
        feed_url = feed_cfg.get("url", "")
        category = feed_cfg.get("category", "")

        if not feed_url:
            logger.warning(f"[{feed_name}] No URL configured, skipping")
            return []

        try:
            raw = await self._download_feed(feed_url)
            parsed = feedparser.parse(raw)

            items: list[SourceItem] = []
            for entry in parsed.entries:
                published = self._parse_date(entry)
                if published and published < cutoff:
                    continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue

                description = entry.get("summary", "") or entry.get("description", "")
                # Strip HTML tags from description for a clean text summary
                if description:
                    from html import unescape
                    import re
                    description = re.sub(r"<[^>]+>", "", unescape(description)).strip()
                    # Truncate overly long descriptions
                    if len(description) > 500:
                        description = description[:497] + "..."

                author = entry.get("author", "") or ""

                items.append(
                    SourceItem(
                        title=title,
                        url=link,
                        source_name=feed_name,
                        description=description,
                        author=author,
                        published=published,
                        extra={"category": category},
                    )
                )

            logger.info(f"[{feed_name}] Parsed {len(items)} items within {self.max_age_days}d window")
            return items

        except Exception as e:
            logger.warning(f"[{feed_name}] Failed to fetch/parse: {e}")
            return []

    @staticmethod
    async def _download_feed(url: str) -> str:
        """Download raw feed XML via aiohttp."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                return await resp.text()

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
        """Try to extract a timezone-aware published datetime from a feed entry."""
        # feedparser normalises dates into published_parsed (time.struct_time)
        for field in ("published_parsed", "updated_parsed"):
            struct = entry.get(field)
            if struct:
                try:
                    from calendar import timegm
                    return datetime.fromtimestamp(timegm(struct), tz=timezone.utc)
                except Exception:
                    pass

        # Fallback: try parsing the raw date string
        for field in ("published", "updated"):
            raw = entry.get(field)
            if raw:
                try:
                    return parsedate_to_datetime(raw).astimezone(timezone.utc)
                except Exception:
                    pass

        return None
