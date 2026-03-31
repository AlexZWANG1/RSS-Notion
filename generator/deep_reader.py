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


def _build_session_with_cookies() -> "requests.Session":
    """Build a requests.Session with YouTube cookies and proxy if available."""
    import http.cookiejar
    import requests

    session = requests.Session()
    cj = http.cookiejar.MozillaCookieJar(_COOKIE_PATH)
    cj.load(ignore_discard=True, ignore_expires=True)
    session.cookies = cj

    # Use proxy if configured (Clash default: 7897)
    proxy_url = os.environ.get("YT_PROXY") or os.environ.get("HTTPS_PROXY", "")
    if not proxy_url:
        # Auto-detect common Clash ports
        import socket
        for port in (7897, 7890, 1080):
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                s.close()
                proxy_url = f"http://127.0.0.1:{port}"
                break
            except OSError:
                continue
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logger.info(f"YouTube proxy: {proxy_url}")

    return session


async def fetch_transcript(video_id: str) -> Optional[str]:
    """Fetch YouTube transcript using youtube-transcript-api v1.x.

    If youtube_cookies.txt exists in project root, uses it to bypass IP blocks.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        loop = asyncio.get_running_loop()

        # Use cookies if available (bypasses YouTube IP blocks)
        if os.path.isfile(_COOKIE_PATH):
            logger.info(f"Using YouTube cookies from {_COOKIE_PATH}")
            session = _build_session_with_cookies()
            api = YouTubeTranscriptApi(http_client=session)
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
你是"深度内容编辑"，参考宝玉(@dotey)的科技博客风格。基于 Sources（播客/视频转写）写一篇信息密度高、可直接发布的长文。像一个认真听完的科技记者在做深度复盘。

来源：{source_name}「{title}」

输出格式（Markdown）：

# 标题（结论式，包含人物名 + 核心判断 + 主题关键词）

**嘉宾简介**（1段：谁，现在做什么，之前做过什么，为什么值得听）

来源：频道/播客名，日期

## 要点速览

- **要点1标题**：一句话展开（4-5个要点，每条加粗小标题+冒号+解释）
- **要点2标题**：...

---

## 正文小节标题1（结论句式，不是问题）

叙事段落 → 引出核心观点 → 双语引文 → 数据/案例支撑 → 分析

> 中文翻译的引文放在这里
> （"English original quote here."）

继续叙事...涉及专业术语时用【注：xxx 指...】补充说明

## 正文小节标题2...

（多个小节按主题推进，不按时间线）

## 结尾

收束主线，读者应带走的核心判断框架

## 话题标签

#标签1 #标签2 ...

---

忠实度要求（最高优先级）：
- 你的唯一信息来源是 transcript，不要添加 transcript 里没有的信息、观点或推测
- 覆盖 transcript 中讨论的每一个主要话题，不跳过任何一段完整讨论，宁长勿短
- 所有观点、判断、预测必须明确归因给具体发言者（"XX认为"/"XX的判断是"），不要写成客观事实
- 引用必须忠实于原话含义，不要扭曲或夸大
- 如果 transcript 中某段内容模糊或可能有识别错误，用【原文不清】标注，不要猜测填补
- 涉及数据/数字时加"据节目提到"来表述，因为你无法核实
- 不要加入你自己的评价或判断

写作规则：
- 小标题必须是结论/判断句（如"设计流程已死，不是自己死的，是被工程速度逼死的"），不要用问句
- 双语引文格式：中文自然翻译在上，英文原文括号在下，使用 blockquote
- 数据用具体数字呈现，善用对比（"从60-70%压缩到30-40%"、"从2-5年缩到3-6个月"）
- 案例要故事化叙述（"有人在Slack里说…然后…结果…"），不要干巴巴列举
- 新概念/产品/人名首次出现时用【注：...】补充
- 重点关键词和产品名**加粗**
- 根据上下文修正转写识别错误，口语改书面
- 开头用一个表格总结所有关键 facts 和数字\
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
    """Convert LLM summary text into Notion blocks, including markdown tables."""
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

    lines = summary.split("\n")
    i = 0
    while i < len(lines):
        if len(blocks) >= 95:
            break

        line = lines[i].strip()
        i += 1

        if not line:
            continue

        # Detect markdown table: line starts with | and contains |
        if line.startswith("|") and "|" in line[1:]:
            # Collect all consecutive table lines
            table_lines = [line]
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1

            # Parse table into Notion table block
            rows: list[list[list[dict]]] = []
            for tl in table_lines:
                # Skip separator lines (|---|---|)
                if re.match(r"^\|[\s\-:|]+\|$", tl):
                    continue
                # Split cells
                cells_raw = [c.strip() for c in tl.split("|")]
                # Remove empty first/last from leading/trailing |
                cells_raw = [c for c in cells_raw if c or cells_raw.index(c) not in (0, len(cells_raw) - 1)]
                cells_raw = [c for c in cells_raw if c != ""]
                if not cells_raw:
                    continue
                row = [_parse_inline_markdown(cell) for cell in cells_raw]
                rows.append(row)

            if rows:
                width = max(len(r) for r in rows)
                # Pad short rows
                for r in rows:
                    while len(r) < width:
                        r.append([{"type": "text", "text": {"content": ""}}])
                table_rows = []
                for row in rows:
                    table_rows.append({
                        "type": "table_row",
                        "table_row": {"cells": row},
                    })
                blocks.append({
                    "type": "table",
                    "table": {
                        "table_width": width,
                        "has_column_header": True,
                        "has_row_header": False,
                        "children": table_rows,
                    },
                })
            continue

        # Headings
        if line.startswith("# ") and not line.startswith("# #"):
            blocks.append({
                "type": "heading_1",
                "heading_1": {"rich_text": _parse_inline_markdown(line[2:])},
            })
        elif line.startswith("### "):
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
        elif line == "---":
            blocks.append({"type": "divider", "divider": {}})
        elif line.startswith("#") and " " in line:
            # Hashtags line — render as gray text
            blocks.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}, "annotations": {"color": "gray"}}]},
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
