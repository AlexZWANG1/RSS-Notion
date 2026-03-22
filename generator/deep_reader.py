"""Deep Reader — fetch YouTube transcripts and generate AI summaries.

Triggered by the 待深度阅读 checkbox in Notion. When a YouTube page is
marked for deep reading, this module:
1. Extracts the video ID from the URL
2. Fetches the transcript (tries zh, en, auto-generated)
3. Sends transcript to LLM for a strategic summary
4. Writes the summary back to the Notion page as structured blocks
"""

import asyncio
import logging
import os
import re
from typing import Optional

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=120.0,
    )


def _extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url or "")
        if m:
            return m.group(1)
    return None


async def fetch_transcript(video_id: str) -> Optional[str]:
    """Fetch YouTube transcript using youtube-transcript-api v1.x."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        loop = asyncio.get_running_loop()
        api = YouTubeTranscriptApi()

        # Try fetching transcript (auto-detects best language)
        try:
            transcript = await loop.run_in_executor(None, lambda: api.fetch(video_id))
            text = " ".join(s.text for s in transcript.snippets)
            if len(text) > 100:
                return text
        except Exception as e:
            logger.info(f"Default transcript failed for {video_id}: {e}")

        # Try listing and picking best available
        try:
            transcript_list = await loop.run_in_executor(None, lambda: api.list(video_id))
            for t in transcript_list:
                try:
                    fetched = await loop.run_in_executor(None, t.fetch)
                    text = " ".join(s.text for s in fetched.snippets)
                    if len(text) > 100:
                        return text
                except Exception:
                    continue
        except Exception:
            pass

        return None

    except ImportError:
        logger.error("youtube-transcript-api not installed")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch transcript for {video_id}: {e}")
        return None


def _build_summary_prompt(title: str, transcript: str, source_name: str) -> str:
    """Build LLM prompt for YouTube video deep summary."""
    # Truncate transcript to ~8000 chars to fit in context
    if len(transcript) > 8000:
        transcript = transcript[:8000] + "\n\n[... transcript truncated ...]"

    return (
        "你是一位AI产业战略分析师的内容助手。\n\n"
        f"以下是来自 {source_name} 的视频「{title}」的完整字幕。\n\n"
        "请生成一篇结构化的深度摘要，面向产品策略师和投资人。\n\n"
        "## 要求\n"
        "1. **核心观点**（3-5 条）：视频中最重要的判断/预测/数据，每条一句话\n"
        "2. **战略含义**（2-3 段）：这些观点对AI产业竞争格局意味着什么\n"
        "3. **关键引用**（2-3 条）：原文中最有信息量的直接引用（中英双语）\n"
        "4. **值得追踪**：基于这个视频，未来几周应该关注什么信号\n\n"
        "## 风格\n"
        "- 用商业语言，不要技术术语\n"
        "- 每段保持2-3句话，不要写长段落\n"
        "- 有数据就引用数据，有人名就写人名\n\n"
        f"## 字幕全文\n\n{transcript}"
    )


async def generate_deep_summary(
    title: str, transcript: str, source_name: str, model: str = "gpt-5.4-mini"
) -> Optional[str]:
    """Generate a deep summary from transcript via LLM."""
    client = _get_client()
    prompt = _build_summary_prompt(title, transcript, source_name)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            timeout=120.0,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM summary failed for '{title}': {e}")
        return None


async def process_deep_read_pages(config: dict) -> int:
    """Find Notion pages marked 待深度阅读 with YouTube URLs, generate summaries.

    Returns number of pages processed.
    """
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return 0

    from delivery.notion_writer import DATABASE_ID

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    llm_cfg = config.get("pipeline", {}).get("llm", {})
    model = llm_cfg.get("processing_model", "gpt-5.4-mini")

    # Query pages with 待深度阅读 = true
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
            headers=headers,
            json={
                "filter": {
                    "property": "待深度阅读",
                    "checkbox": {"equals": True},
                },
            },
        )
        resp.raise_for_status()
        pages = resp.json().get("results", [])

    processed = 0
    for page in pages:
        props = page.get("properties", {})
        page_id = page["id"]

        # Get URL
        url = props.get("原文链接", {}).get("url", "")
        video_id = _extract_video_id(url)
        if not video_id:
            continue

        # Get title
        title_parts = props.get("名称", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)

        # Check if already has deep content (look for existing blocks)
        async with httpx.AsyncClient(timeout=20.0) as client:
            blocks_resp = await client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=headers,
                params={"page_size": 10},
            )
            existing_blocks = blocks_resp.json().get("results", [])
            # If already has more than 5 blocks, skip (already processed)
            if len(existing_blocks) > 5:
                logger.info(f"[Deep Reader] Skipping (already processed): {title}")
                continue

        logger.info(f"[Deep Reader] Processing: {title} ({video_id})")

        # Fetch transcript
        transcript = await fetch_transcript(video_id)
        if not transcript:
            logger.warning(f"[Deep Reader] No transcript available for: {title}")
            # Write a note that transcript wasn't available
            await _append_blocks(page_id, headers, [
                _text_block("⚠️ 字幕不可用，无法生成深度摘要。"),
            ])
            continue

        logger.info(f"[Deep Reader] Got transcript ({len(transcript)} chars), generating summary...")

        # Generate summary
        summary = await generate_deep_summary(title, transcript, "", model=model)
        if not summary:
            continue

        # Write summary blocks to page
        blocks = _build_summary_blocks(summary, video_id)
        await _append_blocks(page_id, headers, blocks)
        processed += 1
        logger.info(f"[Deep Reader] Written summary for: {title}")

    return processed


def _text_block(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }


def _build_summary_blocks(summary: str, video_id: str) -> list[dict]:
    """Convert LLM summary text into Notion blocks."""
    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": "AI 深度摘要（基于视频字幕自动生成）"}}],
            "icon": {"type": "emoji", "emoji": "\U0001f9e0"},
        },
    })

    blocks.append({"type": "divider", "divider": {}})

    # Split summary into paragraphs and write as blocks
    for line in summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(blocks) >= 95:
            break

        # Detect markdown headings
        if line.startswith("## "):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": line[3:]}}]},
            })
        elif line.startswith("### "):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": line[4:]}}]},
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"text": {"content": line[2:][:2000]}}]},
            })
        elif line.startswith("> "):
            blocks.append({
                "type": "quote",
                "quote": {"rich_text": [{"text": {"content": line[2:][:2000]}}]},
            })
        else:
            blocks.append(_text_block(line))

    return blocks[:98]


async def _append_blocks(page_id: str, headers: dict, blocks: list[dict]) -> bool:
    """Append blocks to a Notion page."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.patch(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=headers,
                json={"children": blocks},
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Failed to append blocks to {page_id}: {e}")
        return False
