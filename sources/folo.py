"""Folo RSS source — reads curated RSS items from the Notion inbox database."""

import asyncio
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

# Prefixes to strip from titles
_TITLE_PREFIXES = re.compile(r"^\[(AI精选|视频摘要)\]\s*")


def _plain_text(rich_text_array: list) -> str:
    """Extract plain text from a Notion rich_text property value."""
    return "".join(seg.get("plain_text", "") for seg in rich_text_array)


def _title_text(title_array: list) -> str:
    """Extract plain text from a Notion title property value."""
    return "".join(seg.get("plain_text", "") for seg in title_array)


class FoloSource(BaseSource):
    """Fetches today's RSS-curated items from the Notion inbox database."""

    name = "RSS精选 (Folo)"
    icon = "📰"

    def __init__(self, config: dict):
        super().__init__(config)
        self._database_id = config.get("database_id")

    def _resolve_database_id(self) -> str | None:
        """Return the database ID from config, or fall back to config.json."""
        if self._database_id:
            return self._database_id

        config_path = Path(__file__).resolve().parent.parent / "config.json"
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            db_id = data.get("notion", {}).get("inbox_database_id")
            if db_id:
                return db_id
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[{self.name}] Could not read config.json: {e}")

        return None

    def _query_notion(self, token: str, database_id: str) -> list[dict]:
        """Query Notion database via raw HTTP (notion-client v3 removed databases.query)."""
        import httpx

        today_str = date.today().isoformat()

        response = httpx.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "filter": {
                    "and": [
                        {
                            "property": "收录时间",
                            "date": {"equals": today_str},
                        },
                    ]
                },
            },
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json().get("results", [])

    def _parse_page(self, page: dict) -> SourceItem | None:
        """Convert a single Notion page into a SourceItem."""
        props = page.get("properties", {})

        # Title (名称)
        raw_title = _title_text(props.get("名称", {}).get("title", []))
        if not raw_title:
            return None
        title = _TITLE_PREFIXES.sub("", raw_title).strip()

        # URL (原文链接)
        url = props.get("原文链接", {}).get("url") or ""
        if not url:
            return None

        # Topic (话题)
        topic_obj = props.get("话题", {}).get("select")
        topic = topic_obj["name"] if topic_obj else ""

        # Importance (重要性)
        importance_obj = props.get("重要性", {}).get("select")
        importance = importance_obj["name"] if importance_obj else ""

        # Media source (媒体来源)
        media_source = _plain_text(props.get("媒体来源", {}).get("rich_text", []))

        return SourceItem(
            title=title,
            url=url,
            source_name=media_source or self.name,
            description="",
            extra={
                "importance": importance,
                "topic": topic,
            },
        )

    async def _fetch(self) -> list[SourceItem]:
        token = os.environ.get("NOTION_TOKEN", "")
        if not token:
            logger.warning(
                f"[{self.name}] NOTION_TOKEN not set — skipping Notion query"
            )
            return []

        database_id = self._resolve_database_id()
        if not database_id:
            logger.warning(
                f"[{self.name}] No database ID configured — skipping"
            )
            return []

        loop = asyncio.get_running_loop()
        pages = await loop.run_in_executor(
            None, self._query_notion, token, database_id
        )

        items: list[SourceItem] = []
        for page in pages:
            item = self._parse_page(page)
            if item:
                items.append(item)

        return items
