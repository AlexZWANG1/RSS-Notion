"""Tavily Search source — targeted site search for sources without RSS."""

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
import yaml

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_TAVILY_API_URL = "https://api.tavily.com/search"
_SOURCES_YAML = Path(__file__).resolve().parent.parent / "sources.yaml"


class TavilySearchSource(BaseSource):
    """Search specific sites that lack RSS feeds via Tavily API."""

    name = "Tavily搜索"
    icon = "🔍"

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = os.environ.get("TAVILY_API_KEY", "")
        self.max_per_query: int = config.get("max_per_query", 3)
        self.max_age_days: int = config.get("max_age_days", 7)
        self.site_queries = self._load_site_queries()

    def _load_site_queries(self) -> list[dict]:
        """Load site-specific queries from sources.yaml."""
        try:
            with open(_SOURCES_YAML, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("search", {}).get("site_queries", [])
        except Exception:
            return []

    async def _fetch(self) -> list[SourceItem]:
        if not self.api_key:
            logger.warning(f"[{self.name}] TAVILY_API_KEY not set, skipping")
            return []

        if not self.site_queries:
            logger.warning(f"[{self.name}] No site_queries configured, skipping")
            return []

        seen_urls: set[str] = set()
        items: list[SourceItem] = []

        async with httpx.AsyncClient(timeout=30) as client:
            for sq in self.site_queries:
                site = sq.get("site", "")
                source_name = sq.get("name", site)
                if not site:
                    continue

                query_items = await self._search_site(client, site, source_name)
                for item in query_items:
                    if item.url not in seen_urls:
                        seen_urls.add(item.url)
                        items.append(item)

        return items

    async def _search_site(
        self, client: httpx.AsyncClient, site: str, source_name: str
    ) -> list[SourceItem]:
        """Search a specific site via Tavily."""
        try:
            payload = {
                "api_key": self.api_key,
                "query": f"site:{site}",
                "max_results": self.max_per_query,
                "search_depth": "basic",
                "include_answer": False,
            }
            resp = await client.post(_TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

            results: list[SourceItem] = []
            for r in data.get("results", []):
                title = r.get("title", "").strip()
                url = r.get("url", "")
                if not title or not url:
                    continue

                published = self._parse_date(r.get("published_date"))

                results.append(
                    SourceItem(
                        title=title,
                        url=url,
                        source_name=source_name,
                        description=r.get("content", "").strip()[:500],
                        published=published,
                    )
                )

            logger.info(f"[{self.name}] {source_name} ({site}): {len(results)} items")
            return results

        except Exception as exc:
            logger.warning(f"[{self.name}] Search '{site}' failed: {exc}")
            return []

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
