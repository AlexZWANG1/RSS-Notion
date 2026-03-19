"""YouTube RSS source — fetches recent videos from configured YouTube channels."""

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


class YouTubeSource(BaseSource):
    """Fetch recent videos from YouTube channels via RSS."""

    name = "YouTube"
    icon = "🎬"

    def __init__(self, config: dict):
        super().__init__(config)
        self.channels: list[dict] = config.get("channels", [])
        self.max_age_days: int = config.get("max_age_days", 3)

    async def _fetch(self) -> list[SourceItem]:
        if not self.channels:
            logger.info("[YouTube] No channels configured — skipping")
            return []

        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_channel(session, ch) for ch in self.channels]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[SourceItem] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                ch_name = self.channels[i].get("name", self.channels[i].get("channel_id", "?"))
                logger.warning(f"[YouTube] Failed to fetch {ch_name}: {result}")
            else:
                items.extend(result)

        # Sort by published date descending
        items.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:self.max_items]

    async def _fetch_channel(
        self, session: aiohttp.ClientSession, channel: dict
    ) -> list[SourceItem]:
        channel_id = channel.get("channel_id", "")
        channel_name = channel.get("name", channel_id)

        if not channel_id:
            logger.warning(f"[YouTube] Channel '{channel_name}' missing channel_id — skipping")
            return []

        url = f"{_YT_RSS_BASE}{channel_id}"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RSSNotion/1.0)"}

        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            xml_text = await resp.text()

        return self._parse_feed(xml_text, channel_name)

    def _parse_feed(self, xml_text: str, channel_name: str) -> list[SourceItem]:
        """Parse YouTube Atom feed into SourceItems."""
        root = ET.fromstring(xml_text)
        items: list[SourceItem] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)

        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            title_el = entry.find(f"{{{_ATOM_NS}}}title")
            link_el = entry.find(f"{{{_ATOM_NS}}}link")
            published_el = entry.find(f"{{{_ATOM_NS}}}published")
            video_id_el = entry.find("{http://www.youtube.com/xml/schemas/2015}videoId")

            # Media group for description/thumbnail
            media_group = entry.find(f"{{{_MEDIA_NS}}}group")
            description = ""
            if media_group is not None:
                desc_el = media_group.find(f"{{{_MEDIA_NS}}}description")
                if desc_el is not None and desc_el.text:
                    description = desc_el.text[:500]

            title = title_el.text if title_el is not None and title_el.text else ""
            if not title:
                continue

            # URL
            video_url = ""
            if link_el is not None:
                video_url = link_el.get("href", "")
            if not video_url and video_id_el is not None and video_id_el.text:
                video_url = f"https://www.youtube.com/watch?v={video_id_el.text}"

            # Published date
            published = None
            if published_el is not None and published_el.text:
                try:
                    published = datetime.fromisoformat(
                        published_el.text.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Filter by age
            if published and published < cutoff:
                continue

            # Extract video ID
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
                    extra={
                        "video_id": video_id,
                        "channel": channel_name,
                    },
                )
            )

        return items
