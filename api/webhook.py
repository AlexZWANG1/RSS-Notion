"""Webhook server — watches Notion for "待深度阅读" items and processes them.

How it works:
  1. Server polls Notion inbox every 30s for pages with 状态="待深度阅读"
  2. For each found page, reads the 原文链接 (video URL)
  3. Calls YouTube Deep Reader backend (localhost:3001) for transcript
  4. Sends transcript to LLM for structured summary
  5. Writes 摘要, 洞察, deep summary back to the Notion page
  6. Changes 状态 from "待深度阅读" → "已读"

Usage:
  1. Start YouTube Deep Reader:  cd D:/项目开发/博客自动化阅读 && npm start
  2. Start this watcher:         python -m api.webhook
  3. In Notion, change any item's 状态 to "待深度阅读" → done automatically

Also exposes POST /webhook/deep-read for direct API calls.

Run:  python -m api.webhook
"""

import asyncio
import json
import logging
import os
import re

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="RSS-Notion Deep Reader")

# Config
YT_READER_BASE = os.environ.get("YT_READER_URL", "http://localhost:3001")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
LLM_MODEL = os.environ.get("WEBHOOK_LLM_MODEL", "gpt-5.4")
DATABASE_ID = "d1da0a02-bb0f-4dfd-a7d0-8cf918e6f23c"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))  # seconds


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def _get_notion_client():
    from notion_client import Client
    return Client(auth=os.environ.get("NOTION_TOKEN", ""))


def _query_pending_pages() -> list[dict]:
    """Find pages with 待深度阅读 checkbox = True.

    Uses raw HTTP because notion-client v3 removed databases.query.
    """
    import httpx

    token = os.environ.get("NOTION_TOKEN", "")
    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "filter": {
                "property": "待深度阅读",
                "checkbox": {"equals": True},
            },
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _read_page_info(page: dict) -> dict:
    """Extract relevant fields from a Notion page."""
    props = page.get("properties", {})

    title_parts = props.get("名称", {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts)

    video_url = props.get("原文链接", {}).get("url", "")

    media_parts = props.get("媒体来源", {}).get("rich_text", [])
    channel = "".join(t.get("plain_text", "") for t in media_parts)

    return {
        "page_id": page["id"],
        "title": title,
        "url": video_url,
        "channel": channel,
    }


def _update_notion_page(
    page_id: str, summary: str, insight: str, deep_summary: str, topics: list[str]
):
    """Write summary back to a Notion page and mark as 已读."""
    notion = _get_notion_client()

    props: dict = {
        "待深度阅读": {"checkbox": False},  # uncheck after processing
    }
    if summary:
        props["摘要"] = {"rich_text": [{"text": {"content": summary[:2000]}}]}
    if insight:
        props["洞察"] = {"rich_text": [{"text": {"content": insight[:2000]}}]}

    notion.pages.update(page_id=page_id, properties=props)

    # Append deep summary as page body blocks
    if deep_summary:
        blocks = [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": "AI 深度摘要"}}]},
            }
        ]
        for para in deep_summary.split("\n"):
            para = para.strip()
            if para:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": para[:2000]}}]},
                })
        if topics:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": f"🏷️ {', '.join(topics)}"}}]},
            })
        notion.blocks.children.append(block_id=page_id, children=blocks)

    logger.info(f"  ✅ Written back to Notion: {page_id}")


def _mark_error(page_id: str, error_msg: str):
    """Mark a page as failed — uncheck and note the error in 洞察."""
    try:
        notion = _get_notion_client()
        notion.pages.update(
            page_id=page_id,
            properties={
                "待深度阅读": {"checkbox": False},
                "洞察": {"rich_text": [{"text": {"content": f"⚠️ 深度阅读失败: {error_msg[:500]}"}}]},
            },
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# YouTube + LLM
# ---------------------------------------------------------------------------

def _extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url or "")
        if m:
            return m.group(1)
    return None


async def _fetch_transcript(video_id: str) -> str | None:
    url = f"{YT_READER_BASE}/api/transcript?v={video_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error(f"  Transcript API returned {resp.status}")
                    return None
                data = await resp.json()
                return data.get("text", "")
    except Exception as e:
        logger.error(f"  Failed to fetch transcript: {e}")
        return None


async def _llm_summarize(title: str, channel: str, transcript: str) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL or None,
        timeout=120.0,
    )

    max_chars = 30000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "\n...[truncated]"

    prompt = (
        f"You are analyzing a YouTube video for an AI/tech daily digest reader.\n\n"
        f"**Video**: {title}\n"
        f"**Channel**: {channel}\n\n"
        f"**Transcript**:\n{transcript}\n\n"
        f"Provide a JSON response with:\n"
        f"- summary: string (Chinese, 30-60 chars, crisp one-line summary)\n"
        f"- insight: string (English, one sentence, the key takeaway)\n"
        f"- deep_summary: string (Chinese, 300-600 chars, structured deep summary "
        f"covering key arguments, evidence, and conclusions. Use line breaks between sections.)\n"
        f"- topics: array of 2-4 topic tags\n"
    )

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"  LLM summarize failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Core: process a single page
# ---------------------------------------------------------------------------

async def _process_page(info: dict) -> bool:
    """Process a single 待深度阅读 page. Returns True on success."""
    page_id = info["page_id"]
    title = info["title"]
    video_url = info["url"]
    channel = info["channel"]

    logger.info(f"  Processing: {title}")

    if not video_url:
        _mark_error(page_id, "页面没有原文链接")
        return False

    video_id = _extract_video_id(video_url)
    if not video_id:
        _mark_error(page_id, f"无法从链接提取视频ID: {video_url}")
        return False

    # Fetch transcript
    logger.info(f"  Fetching transcript: {video_id}")
    transcript = await _fetch_transcript(video_id)
    if not transcript:
        _mark_error(page_id, "获取字幕失败（视频可能没有字幕）")
        return False

    logger.info(f"  Got transcript ({len(transcript)} chars), calling LLM...")

    # LLM summarize
    result = await _llm_summarize(title, channel, transcript)
    if not result:
        _mark_error(page_id, "LLM 摘要生成失败")
        return False

    # Write back
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _update_notion_page,
        page_id,
        result.get("summary", ""),
        result.get("insight", ""),
        result.get("deep_summary", ""),
        result.get("topics", []),
    )
    return True


# ---------------------------------------------------------------------------
# Polling watcher (runs as background task)
# ---------------------------------------------------------------------------

async def _poll_loop():
    """Poll Notion every POLL_INTERVAL seconds for 待深度阅读 pages."""
    logger.info(f"🔄 Watcher started — polling every {POLL_INTERVAL}s for '待深度阅读' items")

    while True:
        try:
            loop = asyncio.get_running_loop()
            pages = await loop.run_in_executor(None, _query_pending_pages)

            if pages:
                logger.info(f"📋 Found {len(pages)} pages with 状态=待深度阅读")
                for page in pages:
                    info = _read_page_info(page)
                    try:
                        await _process_page(info)
                    except Exception as e:
                        logger.error(f"  Error processing {info['page_id']}: {e}")
                        _mark_error(info["page_id"], str(e))

        except Exception as e:
            logger.error(f"Poll error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_poll_loop())


# ---------------------------------------------------------------------------
# HTTP endpoints (still available for direct API calls)
# ---------------------------------------------------------------------------

@app.post("/webhook/deep-read")
async def deep_read(request: Request):
    """Direct API: deep-read a specific page by ID."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    page_id = body.get("page_id", "")
    if not page_id:
        return JSONResponse({"error": "Missing page_id"}, status_code=400)

    loop = asyncio.get_running_loop()
    notion = _get_notion_client()
    page = await loop.run_in_executor(None, notion.pages.retrieve, page_id)
    info = _read_page_info(page)

    success = await _process_page(info)
    if success:
        return JSONResponse({"ok": True, "page_id": page_id})
    else:
        return JSONResponse({"error": "Processing failed"}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "poll_interval": POLL_INTERVAL}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEBHOOK_PORT", "8900"))
    logger.info(f"Starting Deep Reader watcher on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
