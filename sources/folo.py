"""Folo source — fetches recent articles from the Folo RSS reader API.

Uses the Folo (follow.is) API to pull articles from the user's
subscriptions. This is the primary RSS source — the user manages
all their RSS/Twitter/YouTube subscriptions in Folo, and the pipeline
pulls unread content from there.

Requires FOLO_SESSION_TOKEN in environment.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from sources.base import BaseSource
from sources.content_fetcher import fetch_content_batch
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_API_BASE = "https://api.follow.is"


class FoloSource(BaseSource):
    """Fetch recent articles from Folo RSS reader subscriptions."""

    name = "Folo"
    icon = "📰"

    def __init__(self, config: dict):
        super().__init__(config)
        self.session_token = os.environ.get("FOLO_SESSION_TOKEN", "")
        self.max_age_days = config.get("max_age_days", 3)

    def _headers(self) -> dict:
        return {
            "Cookie": f"__Secure-better-auth.session_token={self.session_token};",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }

    async def _fetch(self) -> list[SourceItem]:
        if not self.session_token:
            logger.warning("[Folo] FOLO_SESSION_TOKEN not set — skipping")
            return []

        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            # Get all subscriptions to build a feed title map
            subs_map = await self._load_subscriptions(client)

            # Fetch recent entries
            items = await self._fetch_entries(client, subs_map)

        logger.info(f"[Folo] Fetched {len(items)} items from {len(subs_map)} subscriptions")

        # Enrich blog/article items with full text via Jina Reader
        items = await self._enrich_items(items)
        return items

    @staticmethod
    async def _enrich_items(items: list[SourceItem]) -> list[SourceItem]:
        """Enrich items with short descriptions via Jina Reader full text."""
        urls_to_fetch: list[str] = []
        indices: list[int] = []
        for i, item in enumerate(items):
            # Skip items that already have substantial content (e.g. tweets)
            if len(item.description or "") >= 200:
                continue
            # Skip non-http URLs
            if not (item.url or "").startswith("http"):
                continue
            urls_to_fetch.append(item.url)
            indices.append(i)

        if urls_to_fetch:
            logger.info(f"[Folo] Enriching {len(urls_to_fetch)} items with Jina Reader")
            bodies = await fetch_content_batch(urls_to_fetch)
            enriched = 0
            for idx, body in zip(indices, bodies):
                if body and len(body) > len(items[idx].description or ""):
                    items[idx].description = body
                    enriched += 1
            logger.info(f"[Folo] Enriched {enriched}/{len(urls_to_fetch)} items")

        return items

    async def _load_subscriptions(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Load subscription ID → title mapping."""
        try:
            resp = await client.get(f"{_API_BASE}/subscriptions")
            resp.raise_for_status()
            data = resp.json()

            sub_map: dict[str, str] = {}
            for sub in data.get("data", []):
                feed_id = sub.get("feedId", "")
                feeds = sub.get("feeds") or {}
                title = feeds.get("title", "") or sub.get("title", "") or "(unknown)"
                if feed_id:
                    sub_map[feed_id] = title

            return sub_map
        except Exception as e:
            logger.warning(f"[Folo] Failed to load subscriptions: {e}")
            return {}

    async def _fetch_entries(
        self, client: httpx.AsyncClient, subs_map: dict[str, str]
    ) -> list[SourceItem]:
        """Fetch recent entries across all subscriptions."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        items: list[SourceItem] = []
        seen_urls: set[str] = set()

        # Sources that have their own dedicated fetcher — skip in Folo to avoid duplicates
        _SKIP_SOURCES = {"Hacker News", "hacker news", "HackerNews"}

        try:
            resp = await client.post(f"{_API_BASE}/entries", json={"limit": 100})
            resp.raise_for_status()
            data = resp.json()

            for wrapper in data.get("data", []):
                entry = wrapper.get("entries") or {}
                feeds = wrapper.get("feeds") or {}

                # Skip sources that have dedicated API fetchers
                feed_title = feeds.get("title", "")
                if feed_title in _SKIP_SOURCES:
                    continue

                item = self._parse_entry(entry, feeds, cutoff)
                if item and item.url not in seen_urls:
                    seen_urls.add(item.url)
                    items.append(item)

        except Exception as e:
            logger.warning(f"[Folo] Failed to fetch entries: {e}")

        return items

    def _parse_entry(
        self, entry: dict, feeds: dict, cutoff: datetime
    ) -> Optional[SourceItem]:
        """Parse a Folo API entry into a SourceItem."""
        title = (entry.get("title") or "").strip()
        url = (entry.get("url") or entry.get("guid") or "").strip()

        if not title or not url:
            return None

        # Parse published date
        published = None
        pub_str = entry.get("publishedAt") or entry.get("insertedAt") or ""
        if pub_str:
            try:
                published = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Filter by age
        if published and published < cutoff:
            return None

        # Source name from feed info
        source_name = feeds.get("title", "") or "Folo"

        # Description
        description = (entry.get("description") or entry.get("content") or "")[:500]
        if description and "<" in description:
            import re
            from html import unescape
            description = re.sub(r"<[^>]+>", "", unescape(description)).strip()

        author = entry.get("author") or ""

        return SourceItem(
            title=title,
            url=url,
            source_name=source_name,
            description=description,
            author=author,
            published=published,
        )
