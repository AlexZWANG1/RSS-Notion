"""Xiaohongshu (小红书) source via xiaohongshu-mcp server.

Connects to a locally running xiaohongshu-mcp server to search for
AI/tech-related posts. The MCP server must be running at the configured
endpoint (default: http://localhost:18060/mcp).

See: https://github.com/xpzouying/xiaohongshu-mcp
"""

import json
import logging
import os

import httpx

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://localhost:18060/mcp"


class XiaohongshuSource(BaseSource):
    """Fetch AI/tech posts from Xiaohongshu via MCP server."""

    name = "小红书"
    icon = "📕"

    def __init__(self, config: dict):
        super().__init__(config)
        self.mcp_url = config.get(
            "mcp_url",
            os.environ.get("XHS_MCP_URL", DEFAULT_MCP_URL),
        )
        self.keywords = config.get("keywords", ["AI", "大模型", "人工智能", "LLM", "Agent"])
        self._session_id: str | None = None

    async def _init_session(self, client: httpx.AsyncClient) -> bool:
        """Initialize MCP session (required before calling tools)."""
        resp = await client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "rss-notion", "version": "1.0"},
                },
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")
        if not self._session_id:
            logger.warning("[小红书] No session ID returned from MCP server")
            return False

        # Send initialized notification
        await client.post(
            self.mcp_url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "Content-Type": "application/json",
                "Mcp-Session-Id": self._session_id,
            },
        )
        return True

    async def _call_mcp_tool(
        self, client: httpx.AsyncClient, tool_name: str, arguments: dict
    ) -> dict | None:
        """Call a tool on the xiaohongshu-mcp server via MCP JSON-RPC."""
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        resp = await client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.warning("MCP tool %s error: %s", tool_name, data["error"])
            return None
        return data.get("result")

    def _parse_feed_item(self, feed: dict) -> SourceItem | None:
        """Parse a Xiaohongshu feed item into a SourceItem."""
        note_card = feed.get("noteCard", {})

        title = note_card.get("displayTitle", "").strip()
        if not title:
            return None

        note_id = feed.get("id", "")
        url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""

        user = note_card.get("user", {})
        author = user.get("nickname", "") or user.get("nickName", "")

        interact = note_card.get("interactInfo", {})
        liked_count = interact.get("likedCount", "0")
        try:
            likes = int(str(liked_count).replace(",", ""))
        except (ValueError, TypeError):
            likes = 0

        # Build description from available info
        note_type = note_card.get("type", "")
        comment_count = interact.get("commentCount", "0")
        collected_count = interact.get("collectedCount", "0")
        desc = f"[{note_type}] 点赞:{liked_count} 收藏:{collected_count} 评论:{comment_count}"

        return SourceItem(
            title=title,
            url=url,
            source_name="小红书",
            description=desc,
            author=author,
            score=likes,
            extra={
                "xsec_token": feed.get("xsecToken", ""),
                "note_type": note_type,
            },
        )

    async def _fetch(self) -> list[SourceItem]:
        """Search Xiaohongshu for AI/tech content via MCP."""
        all_items: list[SourceItem] = []
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Initialize MCP session
            try:
                ok = await self._init_session(client)
                if not ok:
                    logger.warning("[小红书] Failed to initialize MCP session — skipping")
                    return []
            except Exception:
                logger.warning("[小红书] MCP server not reachable at %s — skipping", self.mcp_url)
                return []

            for keyword in self.keywords:
                try:
                    result = await self._call_mcp_tool(client, "search_feeds", {
                        "keyword": keyword,
                    })
                    if not result:
                        continue

                    content_list = result.get("content", [])
                    for content_block in content_list:
                        if content_block.get("type") != "text":
                            continue
                        try:
                            feeds_data = json.loads(content_block.get("text", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            continue

                        feeds = feeds_data.get("feeds", []) if isinstance(feeds_data, dict) else feeds_data
                        for feed in feeds:
                            feed_id = feed.get("id", "")
                            if feed_id in seen_ids:
                                continue
                            item = self._parse_feed_item(feed)
                            if item:
                                seen_ids.add(feed_id)
                                all_items.append(item)

                except Exception as exc:
                    logger.warning("[小红书] Search for '%s' failed: %s", keyword, exc)

        logger.info("[小红书] Found %d unique items across %d keywords", len(all_items), len(self.keywords))
        return all_items
