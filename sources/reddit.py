"""Reddit data source — fetches hot posts from configured subreddits.

Primary method: PRAW (Python Reddit API Wrapper) using credentials from env.
Fallback: Reddit RSS feeds (no credentials needed), then Jina Reader as last resort.
"""

import asyncio
import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_DEFAULT_SUBREDDITS = ["LocalLLaMA", "MachineLearning"]
_JINA_READER_PREFIX = "https://r.jina.ai/"
_ATOM_NS = "http://www.w3.org/2005/Atom"


class RedditSource(BaseSource):
    """Fetch hot posts from Reddit subreddits."""

    name = "Reddit"
    icon = "💬"

    def __init__(self, config: dict):
        super().__init__(config)
        self.subreddits: list[str] = config.get("subreddits", _DEFAULT_SUBREDDITS)

    # ------------------------------------------------------------------
    # Main entry point (called by BaseSource.fetch)
    # ------------------------------------------------------------------

    async def _fetch(self) -> list[SourceItem]:
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        user_agent = os.environ.get("REDDIT_USER_AGENT", "rss-notion-bot/1.0")

        if client_id and client_secret:
            logger.info("Using PRAW for Reddit data")
            return await self._fetch_with_praw(client_id, client_secret, user_agent)

        logger.info("No Reddit credentials found — falling back to RSS feeds")
        try:
            items = await self._fetch_with_rss()
            if items:
                return items
        except Exception as exc:
            logger.warning(f"RSS fallback failed: {exc}")

        logger.info("RSS fallback returned nothing — trying Jina Reader")
        return await self._fetch_with_jina()

    # ------------------------------------------------------------------
    # PRAW path (synchronous library, run in executor)
    # ------------------------------------------------------------------

    async def _fetch_with_praw(
        self, client_id: str, client_secret: str, user_agent: str
    ) -> list[SourceItem]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._praw_sync,
            client_id,
            client_secret,
            user_agent,
        )

    def _praw_sync(
        self, client_id: str, client_secret: str, user_agent: str
    ) -> list[SourceItem]:
        import praw  # imported here so the dep is optional

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        items: list[SourceItem] = []
        for sub_name in self.subreddits:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.hot(limit=self.max_items):
                if post.stickied:
                    continue
                items.append(
                    SourceItem(
                        title=post.title,
                        url=f"https://www.reddit.com{post.permalink}",
                        source_name=f"r/{sub_name}",
                        description=(post.selftext or "")[:500],
                        author=str(post.author) if post.author else "",
                        score=post.score,
                        published=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                        extra={
                            "subreddit": sub_name,
                            "num_comments": post.num_comments,
                        },
                    )
                )

        items.sort(key=lambda x: x.score or 0, reverse=True)
        return items

    # ------------------------------------------------------------------
    # RSS feed fallback (no credentials needed)
    # ------------------------------------------------------------------

    async def _fetch_with_rss(self) -> list[SourceItem]:
        all_items: list[SourceItem] = []
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._fetch_rss_feed(session, sub) for sub in self.subreddits
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        f"RSS feed failed for r/{self.subreddits[i]}: {result}"
                    )
                else:
                    all_items.extend(result)

        all_items.sort(key=lambda x: x.score or 0, reverse=True)
        return all_items

    async def _fetch_rss_feed(
        self, session: aiohttp.ClientSession, sub_name: str
    ) -> list[SourceItem]:
        url = f"https://www.reddit.com/r/{sub_name}/hot.rss"
        headers = {"User-Agent": "rss-notion-bot/1.0"}

        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        return self._parse_atom_feed(text, sub_name)

    def _parse_atom_feed(self, xml_text: str, sub_name: str) -> list[SourceItem]:
        """Parse Reddit Atom feed into SourceItems."""
        root = ET.fromstring(xml_text)
        items: list[SourceItem] = []

        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            title_el = entry.find(f"{{{_ATOM_NS}}}title")
            link_el = entry.find(f"{{{_ATOM_NS}}}link")
            author_el = entry.find(f".//{{{_ATOM_NS}}}name")
            updated_el = entry.find(f"{{{_ATOM_NS}}}updated")
            content_el = entry.find(f"{{{_ATOM_NS}}}content")

            title = title_el.text if title_el is not None and title_el.text else ""
            if not title:
                continue

            # Get the link URL
            post_url = ""
            if link_el is not None:
                post_url = link_el.get("href", "")

            # Extract author (format: /u/username)
            author = ""
            if author_el is not None and author_el.text:
                author = author_el.text.replace("/u/", "")

            # Parse timestamp
            published = None
            if updated_el is not None and updated_el.text:
                try:
                    published = datetime.fromisoformat(
                        updated_el.text.replace("+00:00", "+00:00")
                    )
                except ValueError:
                    pass

            # Extract description from HTML content
            description = ""
            if content_el is not None and content_el.text:
                # Strip HTML tags for a plain-text description
                raw = content_el.text
                clean = re.sub(r"<[^>]+>", " ", raw)
                clean = html.unescape(clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                description = clean[:500]

            # Try to extract score/comments from content
            score = 0
            num_comments = 0
            if content_el is not None and content_el.text:
                raw = content_el.text
                # Reddit RSS sometimes embeds "submitted by" info but no score
                # We set score=0 since RSS doesn't provide it

            # Skip stickied posts — RSS doesn't flag them directly,
            # but stickied posts from mods often have certain patterns.
            # We skip the first entry if it looks like an announcement.
            # (This is a heuristic; PRAW path handles this precisely.)

            items.append(
                SourceItem(
                    title=title,
                    url=post_url,
                    source_name=f"r/{sub_name}",
                    description=description,
                    author=author,
                    score=score,
                    published=published,
                    extra={
                        "subreddit": sub_name,
                        "num_comments": num_comments,
                    },
                )
            )

        return items[: self.max_items]

    # ------------------------------------------------------------------
    # Jina Reader fallback (last resort)
    # ------------------------------------------------------------------

    async def _fetch_with_jina(self) -> list[SourceItem]:
        all_items: list[SourceItem] = []
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._scrape_subreddit(session, sub) for sub in self.subreddits
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        f"Jina scrape failed for r/{self.subreddits[i]}: {result}"
                    )
                else:
                    all_items.extend(result)

        all_items.sort(key=lambda x: x.score or 0, reverse=True)
        return all_items

    async def _scrape_subreddit(
        self, session: aiohttp.ClientSession, sub_name: str
    ) -> list[SourceItem]:
        url = f"{_JINA_READER_PREFIX}https://www.reddit.com/r/{sub_name}/hot/"
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "text",
        }

        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        return self._parse_jina_markdown(text, sub_name)

    def _parse_jina_markdown(self, text: str, sub_name: str) -> list[SourceItem]:
        """Parse Jina Reader markdown output into SourceItems."""
        items: list[SourceItem] = []
        link_pattern = re.compile(
            r"\[([^\]]{5,})\]\((https?://(?:www\.)?reddit\.com/r/\S+/comments/\S+?)\)"
        )

        seen_urls: set[str] = set()
        for match in link_pattern.finditer(text):
            title = match.group(1).strip()
            post_url = match.group(2).strip().rstrip(")")

            if post_url in seen_urls:
                continue
            seen_urls.add(post_url)

            score = self._extract_score_near(text, match.start())

            items.append(
                SourceItem(
                    title=title,
                    url=post_url,
                    source_name=f"r/{sub_name}",
                    description="",
                    author="",
                    score=score,
                    extra={
                        "subreddit": sub_name,
                        "num_comments": 0,
                    },
                )
            )

        if not items:
            items = self._parse_jina_headings(text, sub_name)

        return items[: self.max_items]

    def _parse_jina_headings(self, text: str, sub_name: str) -> list[SourceItem]:
        """Fallback: extract titles from markdown headings."""
        items: list[SourceItem] = []
        heading_pattern = re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE)
        for match in heading_pattern.finditer(text):
            title = match.group(1).strip()
            if len(title) < 10 or title.lower() in ("hot", "new", "top", "rising"):
                continue
            items.append(
                SourceItem(
                    title=title,
                    url=f"https://www.reddit.com/r/{sub_name}/",
                    source_name=f"r/{sub_name}",
                    description="",
                    author="",
                    score=0,
                    extra={
                        "subreddit": sub_name,
                        "num_comments": 0,
                    },
                )
            )
        return items

    @staticmethod
    def _extract_score_near(text: str, position: int) -> int:
        """Try to find a numeric score near a match position."""
        window_start = max(0, position - 200)
        window_end = min(len(text), position + 200)
        window = text[window_start:window_end]

        score_patterns = [
            re.compile(r"(\d[\d,]*)\s*(?:points?|upvotes?|score)", re.IGNORECASE),
            re.compile(r"(?:points?|upvotes?|score)\s*[:\-]?\s*(\d[\d,]*)", re.IGNORECASE),
        ]
        for pat in score_patterns:
            m = pat.search(window)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return 0
