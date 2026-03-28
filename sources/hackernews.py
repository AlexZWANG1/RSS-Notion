"""Hacker News data source — fetches top stories (no content filtering)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from sources.base import BaseSource
from sources.models import SourceItem
from sources.content_fetcher import fetch_content_batch

logger = logging.getLogger(__name__)

_API_BASE = "https://hacker-news.firebaseio.com/v0/"
_TOP_STORIES_URL = f"{_API_BASE}topstories.json"
_ITEM_URL = f"{_API_BASE}item/{{id}}.json"
_TOP_N = 50  # fetch top 50, let LLM decide what's relevant


class HackerNewsSource(BaseSource):
    """Fetch top stories from Hacker News (no keyword filtering)."""

    name = "Hacker News"
    icon = "🔥"

    async def _fetch(self) -> list[SourceItem]:
        async with aiohttp.ClientSession() as session:
            # 1. Get top story IDs
            async with session.get(_TOP_STORIES_URL) as resp:
                resp.raise_for_status()
                story_ids: list[int] = await resp.json()

            # 2. Fetch details for the top N stories concurrently
            top_ids = story_ids[:_TOP_N]
            tasks = [self._fetch_item(session, sid) for sid in top_ids]
            raw_items: list[Optional[dict]] = await asyncio.gather(*tasks)

            # 3. Build SourceItems — no keyword filtering, LLM decides
            items: list[SourceItem] = []
            for data in raw_items:
                if data is None:
                    continue
                title = data.get("title", "")

                score = data.get("score", 0)
                descendants = data.get("descendants", 0)
                url = data.get("url") or f"https://news.ycombinator.com/item?id={data['id']}"

                published = None
                if "time" in data:
                    published = datetime.fromtimestamp(data["time"], tz=timezone.utc)

                items.append(
                    SourceItem(
                        title=title,
                        url=url,
                        source_name=self.name,
                        description=f"Score: {score} | Comments: {descendants}",
                        author=data.get("by", ""),
                        score=score,
                        published=published,
                    )
                )

            # 4. Sort by score descending
            items.sort(key=lambda x: x.score or 0, reverse=True)

        # 5. Enrich with article body text (outside session block)
        items = await self._enrich_items(items)
        return items

    @staticmethod
    async def _enrich_items(items: list[SourceItem]) -> list[SourceItem]:
        """Enrich items that have URLs with Jina Reader body text."""
        urls_to_fetch = []
        indices = []
        for i, item in enumerate(items):
            if item.url and not item.url.startswith("https://news.ycombinator.com"):
                urls_to_fetch.append(item.url)
                indices.append(i)

        if urls_to_fetch:
            bodies = await fetch_content_batch(urls_to_fetch)
            for idx, body in zip(indices, bodies):
                if body:
                    items[idx].description = body

        return items

    @staticmethod
    async def _fetch_item(session: aiohttp.ClientSession, item_id: int) -> Optional[dict]:
        """Fetch a single HN item, returning None on failure."""
        try:
            url = _ITEM_URL.format(id=item_id)
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch HN item {item_id}: {exc}")
            return None
