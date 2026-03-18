"""GitHub Trending data source — fetches daily trending repos."""

import logging
import re
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)


class GitHubTrendingSource(BaseSource):
    """Fetch trending repositories from GitHub."""

    name = "GitHub Trending"
    icon = "⭐"

    def __init__(self, config: dict):
        super().__init__(config)
        self.language: str = config.get("language", "python")

    # ----- public entry point (called by BaseSource.fetch) -----

    async def _fetch(self) -> list[SourceItem]:
        items = await self._fetch_via_jina()
        if items:
            return items
        logger.warning("[GitHub Trending] Jina Reader failed or empty; falling back to direct scrape")
        return await self._fetch_via_bs4()

    # ----- strategy 1: Jina Reader -----

    async def _fetch_via_jina(self) -> list[SourceItem]:
        url = f"https://r.jina.ai/https://github.com/trending/{self.language}?since=daily"
        headers = {"Accept": "text/plain"}
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"Jina returned status {resp.status}")
                        return []
                    text = await resp.text()
            return self._parse_jina(text)
        except Exception as e:
            logger.warning(f"Jina request failed: {e}")
            return []

    def _parse_jina(self, text: str) -> list[SourceItem]:
        """Parse Jina Reader markdown for trending repos.

        Format produced by Jina for GitHub Trending:
          ## [owner / repo](https://github.com/owner/repo)
          <blank>
          Description text here.
          <blank>
          Python[55,239](...) ... 975 stars today
        """
        items: list[SourceItem] = []

        # Heading pattern: ## [owner / repo](url)
        heading_pattern = re.compile(
            r'^##\s+\[([A-Za-z0-9_.\-]+\s*/\s*[A-Za-z0-9_.\-]+)\]'
            r'\(\s*(https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)\s*\)'
        )
        stars_pattern = re.compile(r'([\d,]+)\s+stars?\s+today', re.IGNORECASE)

        lines = text.splitlines()

        for i, line in enumerate(lines):
            m = heading_pattern.search(line)
            if not m:
                continue

            owner_repo = m.group(1).replace(" ", "")
            repo_url = m.group(2).strip()

            # Look ahead for description and stars (within the next ~6 lines)
            description = ""
            score: Optional[int] = None
            for j in range(i + 1, min(len(lines), i + 7)):
                ahead = lines[j].strip()
                if not ahead:
                    continue
                # Stars line (also contains language, fork count, Built by, etc.)
                sm = stars_pattern.search(ahead)
                if sm:
                    score = int(sm.group(1).replace(",", ""))
                    continue
                # Skip nav/button lines
                if ahead.startswith("[Star]") or ahead.startswith("[Sponsor]"):
                    continue
                # First substantial text line is the description
                if not description and len(ahead) > 10 and not ahead.startswith("Built by"):
                    # Don't treat the metadata line as description
                    if not re.match(r'^[A-Za-z]+\[[\d,]+\]', ahead):
                        description = ahead
                    else:
                        # The metadata line may still contain a description prefix before the language tag
                        pass

            items.append(SourceItem(
                title=owner_repo,
                url=repo_url,
                source_name=self.name,
                description=description,
                score=score,
            ))

        return items

    # ----- strategy 2: direct scrape with BeautifulSoup -----

    async def _fetch_via_bs4(self) -> list[SourceItem]:
        url = f"https://github.com/trending/{self.language}?since=daily"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RSSNotion/1.0)"}
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"GitHub returned status {resp.status}")
                    html = await resp.text()
            return self._parse_html(html)
        except Exception as e:
            logger.error(f"BS4 fallback failed: {e}")
            raise

    def _parse_html(self, html: str) -> list[SourceItem]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[SourceItem] = []

        for article in soup.select("article.Box-row"):
            # Repo name — in an <h2> with an <a> child
            h2 = article.select_one("h2 a")
            if not h2:
                continue
            href = h2.get("href", "").strip()
            owner_repo = href.strip("/")
            repo_url = f"https://github.com/{owner_repo}"

            # Description
            p = article.select_one("p")
            description = p.get_text(strip=True) if p else ""

            # Stars today — look for text like "123 stars today"
            score: Optional[int] = None
            star_span = article.find(string=re.compile(r'stars?\s+today', re.I))
            if star_span:
                m = re.search(r'([\d,]+)', star_span.strip())
                if m:
                    score = int(m.group(1).replace(",", ""))

            items.append(SourceItem(
                title=owner_repo,
                url=repo_url,
                source_name=self.name,
                description=description,
                score=score,
            ))

        return items
