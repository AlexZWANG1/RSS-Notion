"""Deep Reader — fetch YouTube transcripts and generate AI summaries.

Triggered by the 待深度阅读 checkbox in Notion. When a page is
marked for deep reading, this module:
1. Extracts the video ID from the URL (or treats as article URL)
2. Fetches the transcript (YouTube) or full text (articles via Jina Reader)
3. Sends content to LLM for a strategic deep summary
4. Writes the summary back to the Notion page as structured blocks
5. Unchecks the 待深度阅读 checkbox
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
        timeout=180.0,
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


def _is_youtube_url(url: str) -> bool:
    return bool(url and ("youtube.com" in url or "youtu.be" in url))


_COOKIE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "youtube_cookies.txt")


async def fetch_transcript(video_id: str) -> Optional[str]:
    """Fetch YouTube transcript using youtube-transcript-api v1.x.

    If youtube_cookies.txt exists in project root, uses it to bypass IP blocks.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        loop = asyncio.get_running_loop()

        # Use cookies if available (bypasses YouTube IP blocks)
        cookie_path = _COOKIE_PATH if os.path.isfile(_COOKIE_PATH) else None
        if cookie_path:
            logger.info(f"Using YouTube cookies from {cookie_path}")
            api = YouTubeTranscriptApi(cookie_path=cookie_path)
        else:
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


async def fetch_article_text(url: str) -> Optional[str]:
    """Fetch article full text via Jina Reader for non-YouTube URLs."""
    try:
        from sources.content_fetcher import fetch_content
        text = await fetch_content(url, max_chars=30000, timeout=30)
        return text if text and len(text) > 200 else None
    except Exception as e:
        logger.warning(f"Failed to fetch article text for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# LLM summary prompts
# ---------------------------------------------------------------------------

_YOUTUBE_PROMPT = """\
你是一位 AI 产业战略分析师的内容助手。

以下是来自 {source_name} 的视频「{title}」的完整字幕。

请生成一篇结构化的深度摘要，面向产品策略师和投资人。

## 输出结构（严格按顺序）

### 📋 概要信息
用一个简短的信息表格开头：
- 标题 / 来源 / 时长类型（播客/演讲/对谈/教程）
- 核心议题一句话概括

### 🎯 核心观点（3-5 条）
视频中最重要的判断、预测、数据。每条一句话，关键数字和人名 **加粗**。

### 📊 战略含义（2-3 段）
这些观点对 AI 产业竞争格局意味着什么。要有判断，不要只复述。

### 💬 关键引用（2-3 条）
原文中最有信息量的直接引用。格式：
> 英文原文
> ——说话人

中文翻译放在引用上方。

### 🔮 值得追踪
基于这个视频，未来几周应该关注什么信号（2-3 条）。

## 风格要求
- 像付费 newsletter 编辑，不是摘要机器人
- 用商业语言，关键术语保留英文
- 每段 2-3 句话，不要长段落
- 有数据就引用数据，有人名就写人名
- 关键数字、公司名、转折点用 **加粗**\
"""

_ARTICLE_PROMPT = """\
你是一位 AI 产业战略分析师的内容助手。

以下是文章「{title}」（来源：{source_name}）的全文。

请生成一篇结构化的深度摘要，面向产品策略师和投资人。

## 输出结构

### 🎯 核心观点（3-5 条）
文章最重要的判断、数据、结论。每条一句话，关键信息 **加粗**。

### 📊 战略含义（1-2 段）
这些观点对行业意味着什么。

### 💬 关键段落（1-2 条）
原文中信息量最大的段落引用。

### 🔮 值得追踪
基于这篇文章，后续应该关注什么。

## 风格
- 像付费 newsletter 编辑
- 关键数字、公司名用 **加粗**
- 段落简短\
"""


async def generate_deep_summary(
    title: str, content: str, source_name: str,
    is_video: bool = True, model: str = "gpt-5.4",
) -> Optional[str]:
    """Generate a deep summary from transcript/article via LLM."""
    client = _get_client()

    # Truncate to fit context
    max_chars = 30000
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... 内容已截断 ...]"

    if is_video:
        system = _YOUTUBE_PROMPT.format(title=title, source_name=source_name)
    else:
        system = _ARTICLE_PROMPT.format(title=title, source_name=source_name)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"## 全文内容\n\n{content}"},
            ],
            temperature=0.5,
            timeout=180.0,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM summary failed for '{title}': {e}")
        return None


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def _text_block(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }


def _build_summary_blocks(summary: str, is_video: bool = True) -> list[dict]:
    """Convert LLM summary text into Notion blocks."""
    from delivery.notion_writer import _parse_inline_markdown

    blocks: list[dict] = []

    # Header callout
    label = "AI 深度摘要（基于视频字幕自动生成）" if is_video else "AI 深度摘要（基于全文自动生成）"
    blocks.append({
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": label}}],
            "icon": {"type": "emoji", "emoji": "🧠"},
            "color": "purple_background",
        },
    })
    blocks.append({"type": "divider", "divider": {}})

    # Parse markdown into Notion blocks
    for line in summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(blocks) >= 95:
            break

        if line.startswith("### "):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": _parse_inline_markdown(line[4:])},
            })
        elif line.startswith("## "):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": _parse_inline_markdown(line[3:])},
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _parse_inline_markdown(line[2:])},
            })
        elif line.startswith("> "):
            blocks.append({
                "type": "quote",
                "quote": {"rich_text": _parse_inline_markdown(line[2:])},
            })
        else:
            blocks.append({
                "type": "paragraph",
                "paragraph": {"rich_text": _parse_inline_markdown(line)},
            })

    return blocks[:98]


async def _append_blocks(page_id: str, headers: dict, blocks: list[dict]) -> bool:
    """Append blocks to a Notion page."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Batch in groups of 100
            for i in range(0, len(blocks), 100):
                batch = blocks[i:i + 100]
                resp = await client.patch(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    headers=headers,
                    json={"children": batch},
                )
                resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Failed to append blocks to {page_id}: {e}")
        return False


async def _uncheck_deep_read(page_id: str, headers: dict) -> None:
    """Uncheck 待深度阅读 checkbox after processing."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=headers,
                json={"properties": {"待深度阅读": {"checkbox": False}}},
            )
    except Exception as e:
        logger.warning(f"Failed to uncheck 待深度阅读 for {page_id}: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def process_deep_read_pages(config: dict) -> int:
    """Find Notion pages marked 待深度阅读, generate summaries.

    Supports both YouTube videos (transcript) and articles (Jina Reader).
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
    model = llm_cfg.get("summary_model", "gpt-5.4")

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

        # Get title
        title_parts = props.get("名称", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)

        # Get source name
        media_parts = props.get("媒体来源", {}).get("rich_text", [])
        source_name = "".join(t.get("plain_text", "") for t in media_parts)

        # Check if already has deep content
        async with httpx.AsyncClient(timeout=20.0) as client:
            blocks_resp = await client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=headers,
                params={"page_size": 10},
            )
            existing_blocks = blocks_resp.json().get("results", [])
            if len(existing_blocks) > 5:
                logger.info(f"[Deep Reader] Skipping (already processed): {title}")
                await _uncheck_deep_read(page_id, headers)
                continue

        # Determine content type and fetch
        is_video = _is_youtube_url(url)
        video_id = _extract_video_id(url) if is_video else None

        if is_video and video_id:
            logger.info(f"[Deep Reader] Processing YouTube: {title} ({video_id})")
            content = await fetch_transcript(video_id)
            if not content:
                logger.warning(f"[Deep Reader] No transcript for: {title}")
                await _append_blocks(page_id, headers, [
                    _text_block("⚠️ YouTube 字幕不可用（可能是 IP 限制或视频没有字幕）"),
                ])
                await _uncheck_deep_read(page_id, headers)
                continue
        elif url:
            logger.info(f"[Deep Reader] Processing article: {title}")
            content = await fetch_article_text(url)
            if not content:
                logger.warning(f"[Deep Reader] Cannot fetch article text: {title}")
                await _append_blocks(page_id, headers, [
                    _text_block("⚠️ 无法获取文章全文"),
                ])
                await _uncheck_deep_read(page_id, headers)
                continue
        else:
            logger.warning(f"[Deep Reader] No URL for: {title}")
            await _uncheck_deep_read(page_id, headers)
            continue

        logger.info(f"[Deep Reader] Got content ({len(content)} chars), generating summary...")

        # Generate summary
        summary = await generate_deep_summary(
            title, content, source_name, is_video=is_video, model=model,
        )
        if not summary:
            await _uncheck_deep_read(page_id, headers)
            continue

        # Write summary blocks to page
        blocks = _build_summary_blocks(summary, is_video=is_video)
        await _append_blocks(page_id, headers, blocks)

        # Uncheck checkbox
        await _uncheck_deep_read(page_id, headers)

        processed += 1
        logger.info(f"[Deep Reader] Done: {title}")

    return processed
