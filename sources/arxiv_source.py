"""arXiv paper source using the arxiv Python package."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import arxiv

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = ["cs.AI", "cs.CL", "cs.LG"]


class ArxivSource(BaseSource):
    """Fetch recent papers from arXiv."""

    name = "arXiv"
    icon = "📄"

    def __init__(self, config: dict):
        super().__init__(config)
        self.categories: list[str] = config.get("categories", DEFAULT_CATEGORIES)
        self.max_items: int = config.get("max_items", 20)

    # ------------------------------------------------------------------

    def _build_query(self) -> str:
        return " OR ".join(f"cat:{cat}" for cat in self.categories)

    def _sync_fetch(self) -> list[SourceItem]:
        """Synchronous fetch – will be called inside an executor."""
        query = self._build_query()
        logger.info(f"[arXiv] query: {query}")

        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=self.max_items * 3,  # over-fetch to allow date filter
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        items: list[SourceItem] = []

        for result in client.results(search):
            published = result.published.replace(tzinfo=timezone.utc)
            if published < cutoff:
                continue

            authors = ", ".join(a.name for a in result.authors[:3])
            if len(result.authors) > 3:
                authors += f" et al. ({len(result.authors)} authors)"

            abstract = result.summary.replace("\n", " ").strip()
            if len(abstract) > 500:
                abstract = abstract[:497] + "..."

            items.append(
                SourceItem(
                    title=result.title,
                    url=result.entry_id,
                    source_name=self.name,
                    description=abstract,
                    author=authors,
                    published=published,
                    extra={
                        "categories": result.categories,
                        "pdf_url": result.pdf_url,
                    },
                )
            )

            if len(items) >= self.max_items:
                break

        return items

    async def _fetch(self) -> list[SourceItem]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_fetch)
