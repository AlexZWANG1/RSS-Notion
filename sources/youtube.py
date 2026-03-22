"""YouTube source — fetches recent videos via RSS, falls back to Jina Reader."""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

_YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
_ATOM_NS = "http://www.w3.org/2005/Atom"
_MEDIA_NS = "http://search.yahoo.com/mrss/"
_JINA_BASE = "https://r.jina.ai/https://www.youtube.com/@{handle}/videos"


class YouTubeSource(BaseSource):
    """Fetch recent videos from YouTube channels via RSS → Jina Reader fallback."""

    name = "YouTube"
    icon = "🎬"

    def __init__(self, config: dict):
        super().__init__(config)
        self.channels: list[dict] = config.get("channels", [])
        self.max_age_days: int = config.get("max_age_days", 7)

    async def _fetch(self) -> list[SourceItem]:
        if not self.channels:
            return []

        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_channel(session, ch) for ch in self.channels]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[SourceItem] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                ch_name = self.channels[i].get("name", "?")
                logger.warning(f"[YouTube] Failed to fetch {ch_name}: {result}")
            else:
                items.extend(result)

        items.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:self.max_items]

    async def _fetch_channel(
        self, session: aiohttp.ClientSession, channel: dict
    ) -> list[SourceItem]:
        channel_id = channel.get("channel_id", "")
        channel_name = channel.get("name", channel_id)
        handle = channel.get("handle", "")

        # Try RSS first
        if channel_id:
            try:
                items = await self._fetch_rss(session, channel_id, channel_name)
                if items:
                    return items
            except Exception as e:
                logger.info(f"[YouTube] RSS failed for {channel_name}: {e}")

        # Fallback: Jina Reader on channel page
        if handle or channel_name:
            try:
                return await self._fetch_jina(session, channel, channel_name)
            except Exception as e:
                logger.warning(f"[YouTube] Jina fallback failed for {channel_name}: {e}")

        return []

    async def _fetch_rss(
        self, session: aiohttp.ClientSession, channel_id: str, channel_name: str
    ) -> list[SourceItem]:
        """Try the standard YouTube RSS feed."""
        url = f"{_YT_RSS_BASE}{channel_id}"
        async with session.get(
            url, headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            xml_text = await resp.text()

        return self._parse_atom(xml_text, channel_name)

    async def _fetch_jina(
        self, session: aiohttp.ClientSession, channel: dict, channel_name: str
    ) -> list[SourceItem]:
        """Fallback: scrape channel page via Jina Reader."""
        handle = channel.get("handle", "")
        if not handle:
            # Try to construct from channel name
            handle = channel_name.replace(" ", "")

        url = f"https://r.jina.ai/https://www.youtube.com/@{handle}/videos"
        async with session.get(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        return self._parse_jina_output(text, channel_name)

    def _parse_jina_output(self, text: str, channel_name: str) -> list[SourceItem]:
        """Parse Jina Reader markdown output for video titles and URLs."""
        items: list[SourceItem] = []
        seen_urls: set[str] = set()
        # Pattern: [Title](URL) — may include quotes in URL
        pattern = re.compile(
            r'\[([^\]]+)\]\((https://www\.youtube\.com/watch\?v=[^\s")]+)'
        )

        for match in pattern.finditer(text):
            title = match.group(1).strip()
            url = match.group(2).strip().rstrip('"')

            # Skip noise: timestamps, shorts, "Now playing" entries
            if "/shorts/" in url:
                continue
            if re.match(r'^[\d:]+\s', title) or "Now playing" in title:
                continue
            if not title or len(title) < 5:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            items.append(
                SourceItem(
                    title=title,
                    url=url,
                    source_name=channel_name,
                    description="",
                    extra={"channel": channel_name},
                )
            )

        logger.info(f"[YouTube] Jina parsed {len(items)} videos for {channel_name}")
        return items[:5]

    def _parse_atom(self, xml_text: str, channel_name: str) -> list[SourceItem]:
        """Parse YouTube Atom feed into SourceItems."""
        root = ET.fromstring(xml_text)
        items: list[SourceItem] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)

        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            title_el = entry.find(f"{{{_ATOM_NS}}}title")
            link_el = entry.find(f"{{{_ATOM_NS}}}link")
            published_el = entry.find(f"{{{_ATOM_NS}}}published")
            video_id_el = entry.find("{http://www.youtube.com/xml/schemas/2015}videoId")

            media_group = entry.find(f"{{{_MEDIA_NS}}}group")
            description = ""
            if media_group is not None:
                desc_el = media_group.find(f"{{{_MEDIA_NS}}}description")
                if desc_el is not None and desc_el.text:
                    description = desc_el.text[:500]

            title = title_el.text if title_el is not None and title_el.text else ""
            if not title:
                continue

            video_url = ""
            if link_el is not None:
                video_url = link_el.get("href", "")
            if not video_url and video_id_el is not None and video_id_el.text:
                video_url = f"https://www.youtube.com/watch?v={video_id_el.text}"

            published = None
            if published_el is not None and published_el.text:
                try:
                    published = datetime.fromisoformat(
                        published_el.text.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            if published and published < cutoff:
                continue

            video_id = ""
            if video_id_el is not None and video_id_el.text:
                video_id = video_id_el.text
            elif video_url:
                m = re.search(r"[?&]v=([^&]+)", video_url)
                if m:
                    video_id = m.group(1)

            items.append(
                SourceItem(
                    title=title,
                    url=video_url,
                    source_name=channel_name,
                    description=description,
                    published=published,
                    extra={"video_id": video_id, "channel": channel_name},
                )
            )

        return items
