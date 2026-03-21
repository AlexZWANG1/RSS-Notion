"""Tavily Search source — gap-filling content discovery via search API."""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_TAVILY_API_URL = "https://api.tavily.com/search"


class TavilySearchSource(BaseSource):
    """Search-based source using the Tavily API for gap-filling content discovery."""

    name = "Tavily搜索"
    icon = "🔍"

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = os.environ.get("TAVILY_API_KEY", "")
        self.queries: list[str] = config.get("queries", [])
        self.max_per_query: int = config.get("max_per_query", 10)
        self.max_age_days: int = config.get("max_age_days", 3)

    async def _fetch(self) -> list[SourceItem]:
        if not self.api_key:
            logger.warning(f"[{self.name}] TAVILY_API_KEY not set, skipping")
            return []

        if not self.queries:
            logger.warning(f"[{self.name}] No queries configured, skipping")
            return []

        seen_urls: set[str] = set()
        items: list[SourceItem] = []
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.max_age_days)

        async with httpx.AsyncClient(timeout=30) as client:
            for query in self.queries:
                query_items = await self._search_query(client, query, cutoff)
                for item in query_items:
                    if item.url not in seen_urls:
                        seen_urls.add(item.url)
                        items.append(item)

        return items

    async def _search_query(
        self, client: httpx.AsyncClient, query: str, cutoff: datetime
    ) -> list[SourceItem]:
        """Execute a single search query, returning SourceItems. Errors are caught per-query."""
        try:
            payload = {
                "api_key": self.api_key,
                "query": query,
                "max_results": self.max_per_query,
                "search_depth": "basic",
                "include_answer": False,
            }
            resp = await client.post(_TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

            results: list[SourceItem] = []
            for r in data.get("results", []):
                published = self._parse_date(r.get("published_date"))

                # Skip results older than max_age_days (if date is available)
                if published and published < cutoff:
                    continue

                results.append(
                    SourceItem(
                        title=r.get("title", "").strip(),
                        url=r.get("url", ""),
                        source_name=self.name,
                        description=r.get("content", "").strip(),
                        published=published,
                        extra={"query": query},
                    )
                )
            return results

        except Exception as exc:
            logger.error(f"[{self.name}] Query '{query}' failed: {exc}")
            return []

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        """Best-effort parse of published_date from Tavily results."""
        if not date_str:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
