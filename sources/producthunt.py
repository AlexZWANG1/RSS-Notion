"""Product Hunt data source — fetches today's AI-related products."""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

# GraphQL endpoint & auth
_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

# Topic slugs considered AI-related
_AI_TOPICS: set[str] = {
    "artificial intelligence",
    "ai",
    "machine learning",
    "developer tools",
    "productivity",
    "saas",
    "tech",
}

# Extra keywords checked against product name + tagline
_AI_NAME_RE = re.compile(
    r"\b(ai|gpt|llm|machine\s*learning|agent)\b",
    re.IGNORECASE,
)

# GraphQL query for today's posts
_POSTS_QUERY = """\
query TodayPosts($first: Int!) {
  posts(order: VOTES, first: $first) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        createdAt
        topics {
          edges {
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""

# Jina Reader fallback
_JINA_URL = "https://r.jina.ai/https://www.producthunt.com/"

# Patterns for parsing Jina markdown output
_PRODUCT_RE = re.compile(
    r"^\s*#{1,3}\s*\[?([^\]\n]+)\]?\(?([^\)\n]*)\)?"
    r".*$",
    re.MULTILINE,
)


class ProductHuntSource(BaseSource):
    """Fetch today's AI-related products from Product Hunt."""

    name = "Product Hunt"
    icon = "📦"

    async def _fetch(self) -> list[SourceItem]:
        token = os.environ.get("PRODUCTHUNT_TOKEN", "")
        if token:
            try:
                return await self._fetch_graphql(token)
            except Exception as exc:
                logger.warning(f"[Product Hunt] GraphQL API failed: {exc}; falling back to Jina Reader")

        return await self._fetch_jina()

    # ------------------------------------------------------------------
    # Primary: GraphQL API
    # ------------------------------------------------------------------
    async def _fetch_graphql(self, token: str) -> list[SourceItem]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "query": _POSTS_QUERY,
            "variables": {"first": 40},
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(_GRAPHQL_URL, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()

        edges = data.get("data", {}).get("posts", {}).get("edges", [])
        items: list[SourceItem] = []

        for edge in edges:
            node = edge.get("node", {})
            topics = [
                t["node"]["name"]
                for t in node.get("topics", {}).get("edges", [])
            ]
            topic_names_lower = {t.lower() for t in topics}

            name = node.get("name", "")
            tagline = node.get("tagline", "")
            combined_text = f"{name} {tagline}"

            # Filter: topic match OR keyword match in name/tagline
            has_ai_topic = bool(topic_names_lower & _AI_TOPICS)
            has_ai_keyword = bool(_AI_NAME_RE.search(combined_text))
            if not (has_ai_topic or has_ai_keyword):
                continue

            url = node.get("website") or node.get("url", "")
            votes = node.get("votesCount", 0)
            description_full = node.get("description", "")

            published = None
            created = node.get("createdAt")
            if created:
                try:
                    published = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            items.append(
                SourceItem(
                    title=name,
                    url=url,
                    source_name=self.name,
                    description=tagline,
                    score=votes,
                    published=published,
                    extra={
                        "topics": topics,
                        "full_description": description_full,
                    },
                )
            )

        items.sort(key=lambda x: x.score or 0, reverse=True)
        return items

    # ------------------------------------------------------------------
    # Fallback: Jina Reader scrape
    # ------------------------------------------------------------------
    async def _fetch_jina(self) -> list[SourceItem]:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            "Accept": "text/markdown",
            "X-Return-Format": "markdown",
            "User-Agent": "Mozilla/5.0",
        }

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_JINA_URL, headers=headers) as resp:
                resp.raise_for_status()
                markdown = await resp.text()

        # Detect Cloudflare / CAPTCHA blocks
        if "security verification" in markdown.lower() or len(markdown) < 600:
            logger.warning("[Product Hunt] Jina Reader returned a blocked/empty page")
            return []

        return self._parse_jina_markdown(markdown)

    def _parse_jina_markdown(self, md: str) -> list[SourceItem]:
        """Parse Jina Reader markdown into SourceItems.

        Jina returns Product Hunt's front page as markdown.  Products
        typically appear as lines with a title/link and a short tagline
        on the next line.  We use a best-effort heuristic.
        """
        items: list[SourceItem] = []
        lines = md.splitlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Try to find markdown links: [Title](url)
            link_match = re.search(r"\[([^\]]{3,})\]\((https?://[^\)]+)\)", line)
            if link_match:
                title = link_match.group(1).strip()
                url = link_match.group(2).strip()

                # Skip navigation / boilerplate links
                title_lower = title.lower()
                if len(title) < 4 or title_lower in (
                    "product hunt", "launches", "products", "news",
                    "community", "advertise", "about", "sign up",
                    "faq", "changelog", "more",
                ) or title_lower.startswith("image ") or title_lower.startswith("icon "):
                    i += 1
                    continue

                # Look ahead for a tagline (non-empty, non-link line)
                tagline = ""
                for j in range(i + 1, min(i + 4, len(lines))):
                    candidate = lines[j].strip()
                    if not candidate or candidate.startswith("[") or candidate.startswith("#"):
                        continue
                    # Strip leading markdown artifacts
                    candidate = re.sub(r"^[>\-\*]+\s*", "", candidate)
                    if len(candidate) > 10:
                        tagline = candidate[:200]
                        break

                # Extract vote count if present nearby
                score = self._extract_votes(lines, i)

                items.append(
                    SourceItem(
                        title=title,
                        url=url,
                        source_name=self.name,
                        description=tagline,
                        score=score,
                        extra={},
                    )
                )
            i += 1

        # Deduplicate by title
        seen: set[str] = set()
        deduped: list[SourceItem] = []
        for item in items:
            key = item.title.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        deduped.sort(key=lambda x: x.score or 0, reverse=True)
        return deduped

    @staticmethod
    def _extract_votes(lines: list[str], idx: int) -> Optional[int]:
        """Try to find a vote count near the given line index."""
        window = "\n".join(lines[max(0, idx - 1): min(len(lines), idx + 5)])
        m = re.search(r"(\d+)\s*(?:upvotes?|votes?)", window, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # Also look for bare numbers that might be vote counts
        m = re.search(r"\b(\d{2,5})\b", window)
        if m:
            val = int(m.group(1))
            if 10 <= val <= 99999:
                return val
        return None
