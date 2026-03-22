"""Score and curate source items against user interests loaded from Notion config page.

The LLM acts as an editorial curator — it decides what to include, how to
classify, and how important each item is.  No hardcoded topic lists, content
types, or numeric thresholds.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

from sources.models import SourceItem

logger = logging.getLogger(__name__)

CONFIG_PAGE_ID = "32516831-83e6-8100-b28f-f60937b0d472"
RESEARCH_DB_ID = "2fe16831-83e6-805c-a095-000bab8d1eca"

DEFAULT_KEYWORDS = [
    "AI", "LLM", "GPT", "agent", "RAG", "transformer", "diffusion",
    "fine-tuning", "RLHF", "MCP", "tool use", "function calling",
    "embedding", "vector database", "prompt engineering", "cloud native",
]

DEFAULT_TOPICS = [
    "AI Agent 基础设施",
    "大模型应用与产品",
    "开源模型与工具",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UserInterests:
    """User interest configuration parsed from Notion."""
    perspective: str = "产品人"
    topics: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICS))
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    designated_topic: Optional[str] = None
    research_titles: list[str] = field(default_factory=list)


@dataclass
class ScoredItem:
    """A source item with LLM-driven editorial curation."""
    original: SourceItem
    include: bool = False
    channel: str = ""          # 一手/深度研究 | 长内容 | 社交/社区 | 开源/论文
    importance: str = "中"
    event_cluster: str = ""
    what_happened: str = ""    # → Notion 摘要
    why_it_matters: str = ""   # → Notion 洞察
    score_reason: str = ""     # → Notion 入选理由


@dataclass
class UserFeedback:
    """Recent user behavior signals from Notion."""
    favorited: list[str] = field(default_factory=list)   # titles user starred
    ignored: list[str] = field(default_factory=list)     # titles user skipped
    deep_read: list[str] = field(default_factory=list)   # titles marked for deep read


# ---------------------------------------------------------------------------
# OpenAI client (same pattern as summarizer.py)
# ---------------------------------------------------------------------------

def _get_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client, respecting OPENAI_BASE_URL for local proxies."""
    return AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=60.0,
    )


async def _call_with_retry(
    client: AsyncOpenAI,
    messages: list[dict],
    model: str,
    temperature: float,
    max_retries: int,
    response_format: Optional[dict] = None,
) -> Optional[str]:
    """Call the OpenAI API with exponential backoff retry."""
    backoff_seconds = [1, 4, 16]

    for attempt in range(max_retries + 1):
        try:
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "timeout": 60.0,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format

            response = await client.chat.completions.create(**kwargs)
            return response.choices[0].message.content

        except Exception as exc:
            if attempt < max_retries:
                delay = backoff_seconds[attempt]
                logger.warning(
                    "OpenAI call failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "OpenAI call failed after %d attempts: %s",
                    max_retries + 1,
                    exc,
                )
                return None


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def _get_notion_client():
    """Create a synchronous Notion client."""
    import notion_client  # type: ignore

    token = os.environ.get("NOTION_TOKEN", "")
    return notion_client.Client(auth=token)


def _extract_rich_text(block_or_prop) -> str:
    """Extract plain text from a Notion rich_text array."""
    if isinstance(block_or_prop, list):
        return "".join(rt.get("plain_text", "") for rt in block_or_prop).strip()
    return ""


def _parse_config_blocks(blocks: list[dict]) -> dict[str, str]:
    """Walk page blocks and map section headings to their body text."""
    sections: dict[str, str] = {}
    current_heading: Optional[str] = None
    current_lines: list[str] = []

    for block in blocks:
        btype = block.get("type", "")

        if btype in ("heading_1", "heading_2", "heading_3"):
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines).strip()
            heading_data = block.get(btype, {})
            current_heading = _extract_rich_text(heading_data.get("rich_text", []))
            current_lines = []
            continue

        text = ""
        if btype == "paragraph":
            text = _extract_rich_text(block.get("paragraph", {}).get("rich_text", []))
        elif btype == "bulleted_list_item":
            text = _extract_rich_text(
                block.get("bulleted_list_item", {}).get("rich_text", [])
            )
        elif btype == "numbered_list_item":
            text = _extract_rich_text(
                block.get("numbered_list_item", {}).get("rich_text", [])
            )

        if text and current_heading is not None:
            current_lines.append(text)

    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


def _fetch_config_page(notion, page_id: str) -> dict[str, str]:
    """Fetch and parse the config page blocks (synchronous)."""
    results = []
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=page_id, start_cursor=cursor, page_size=100
        )
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return _parse_config_blocks(results)


def _fetch_research_titles(notion, database_id: str) -> list[str]:
    """Query the research database for existing topic titles (synchronous)."""
    titles: list[str] = []
    try:
        cursor = None
        while True:
            kwargs: dict = {
                "database_id": database_id,
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor

            resp = notion.databases.query(**kwargs)
            for page in resp.get("results", []):
                props = page.get("properties", {})
                for key in ("Name", "名称", "title", "Title"):
                    title_prop = props.get(key)
                    if title_prop and title_prop.get("type") == "title":
                        text = _extract_rich_text(title_prop.get("title", []))
                        if text:
                            titles.append(text)
                        break

            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

    except Exception as exc:
        logger.warning("Failed to fetch research database titles: %s", exc)

    return titles


def _fetch_recent_feedback(notion, database_id: str, days: int = 7) -> UserFeedback:
    """Fetch recent user behavior from Notion inbox/archive for feedback loop."""
    from datetime import date, timedelta
    import httpx

    feedback = UserFeedback()
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return feedback

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    try:
        # Query recent pages with 收录时间 in last N days
        resp = httpx.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "filter": {
                    "property": "收录时间",
                    "date": {"on_or_after": cutoff},
                },
                "page_size": 100,
            },
            timeout=20.0,
        )
        resp.raise_for_status()

        for page in resp.json().get("results", []):
            props = page.get("properties", {})

            # Extract title
            title_parts = props.get("名称", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts)
            # Strip [Source] prefix for cleaner matching
            import re
            title = re.sub(r"^\[[^\]]+\]\s*", "", title)

            if not title or title.startswith("[运行报告]"):
                continue

            # Check 选择 (收藏/不收藏)
            choice_sel = props.get("选择", {}).get("select")
            choice = choice_sel["name"] if choice_sel else ""

            # Check 待深度阅读
            deep_read = props.get("待深度阅读", {}).get("checkbox", False)

            if choice == "收藏":
                feedback.favorited.append(title)
            elif choice == "不收藏":
                feedback.ignored.append(title)

            if deep_read:
                feedback.deep_read.append(title)

    except Exception as exc:
        logger.warning("Failed to fetch user feedback: %s", exc)

    logger.info(
        "Loaded feedback: %d favorited, %d ignored, %d deep-read",
        len(feedback.favorited), len(feedback.ignored), len(feedback.deep_read),
    )
    return feedback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_user_interests(config: dict | None = None) -> UserInterests:
    """Load user interest configuration from Notion config page."""
    if not os.environ.get("NOTION_TOKEN"):
        logger.info("NOTION_TOKEN not set — using default interests")
        return UserInterests()

    notion_cfg = (config or {}).get("notion", {})
    page_id = notion_cfg.get("config_page_id", CONFIG_PAGE_ID)
    research_db = notion_cfg.get("research_database_data_source", "")
    research_id = research_db.replace("collection://", "") if research_db else RESEARCH_DB_ID

    loop = asyncio.get_running_loop()

    try:
        notion = _get_notion_client()

        sections_future = loop.run_in_executor(
            None, _fetch_config_page, notion, page_id
        )
        titles_future = loop.run_in_executor(
            None, _fetch_research_titles, notion, research_id
        )

        sections, research_titles = await asyncio.gather(
            sections_future, titles_future
        )

        perspective = sections.get("筛选视角", "产品人").strip()

        topics_raw = sections.get("长期关注课题", "")
        topics = [
            line.strip("- •").strip()
            for line in topics_raw.split("\n")
            if line.strip()
        ] or list(DEFAULT_TOPICS)

        keywords_raw = sections.get("关键词表", "")
        keywords = [
            kw.strip()
            for kw in keywords_raw.replace("\n", ",").replace("，", ",").split(",")
            if kw.strip()
        ] or list(DEFAULT_KEYWORDS)

        designated_topic_raw = sections.get("指定课题", "").strip()
        designated_topic = designated_topic_raw if designated_topic_raw else None

        return UserInterests(
            perspective=perspective,
            topics=topics,
            keywords=keywords,
            designated_topic=designated_topic,
            research_titles=research_titles,
        )

    except Exception as exc:
        logger.error("Failed to load interests from Notion: %s — using defaults", exc)
        return UserInterests()


def _pre_filter(items: list[SourceItem]) -> list[SourceItem]:
    """Python-level URL/title dedup before sending to LLM."""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[SourceItem] = []
    for item in items:
        norm_url = item.url.rstrip("/").lower()
        norm_title = item.title.strip().lower()
        if norm_url in seen_urls or norm_title in seen_titles:
            continue
        seen_urls.add(norm_url)
        seen_titles.add(norm_title)
        result.append(item)
    return result


def _build_scoring_prompt(items, interests, feedback=None):
    entries = []
    for i, item in enumerate(items):
        entries.append(
            f"[{i}] {item.title}\n"
            f"    source: {item.source_name}\n"
            f"    desc: {item.description[:400]}\n"
            f"    url: {item.url}"
        )
    items_text = "\n\n".join(entries)
    topics_text = ", ".join(interests.topics)

    designated = ""
    if interests.designated_topic:
        designated = f"\n⚡ 今日指定关注：{interests.designated_topic}（相关内容强烈倾向入选）\n"

    feedback_section = ""
    if feedback and (feedback.favorited or feedback.ignored):
        lines = []
        for t in feedback.favorited[:10]:
            lines.append(f"  ✅ {t}")
        for t in feedback.ignored[:10]:
            lines.append(f"  ❌ {t}")
        feedback_section = "\n## 读者最近的行为\n" + "\n".join(lines) + "\n先想想这位读者的口味，再往下做筛选。\n"

    return (
        f"你是一位信息策展人，服务于一位关注AI产业的{interests.perspective}。\n\n"
        f"## 读者\n关注：{topics_text}\n{designated}{feedback_section}\n"
        f"## 任务\n\n"
        f"你面前有 {len(items)} 条候选内容。\n\n"
        "1. 找出讲同一件事的重复报道，标同一个 event_cluster，只 include 信息量最大的那条\n"
        "2. 从去重后的内容中选 10-15 条值得读者时间的。宁缺毋滥\n"
        "3. 入选的按重要性从高到低排列\n\n"
        "好内容 = 有读者昨天不知道的事实/数据/判断 + 会影响对某个公司/赛道/技术的看法\n"
        "不选 = 标题说完了全部信息 / 二手转述无新观点 / 教程how-to / 跟科技无关\n\n"
        "## 输出JSON\n\n"
        '返回 {"items": [...]} 数组。\n\n'
        "入选的每条：\n"
        "{\n"
        '  "index": 0,\n'
        '  "include": true,\n'
        '  "event_cluster": "事件名或空字符串",\n'
        '  "channel": "一手/深度研究 | 长内容 | 社交/社区 | 开源/论文",\n'
        '  "what_happened": "中文30-50字。谁做了什么，关键数字",\n'
        '  "why_it_matters": "中文30-80字。这改变了什么判断、打破了什么预期、确认了什么趋势",\n'
        '  "reason": "一句话为什么选"\n'
        "}\n\n"
        "未入选的只要：\n"
        '{ "index": 5, "include": false, "reason": "一句话" }\n\n'
        f"## 候选（{len(items)}条）\n\n{items_text}"
    )


def _fallback_scored_item(item: SourceItem) -> ScoredItem:
    """Create a minimal ScoredItem when LLM scoring fails."""
    return ScoredItem(original=item, include=False, importance="低")


async def load_user_feedback(config: dict | None = None) -> UserFeedback:
    """Load recent user behavior from Notion for feedback-driven curation."""
    if not os.environ.get("NOTION_TOKEN"):
        return UserFeedback()

    from delivery.notion_writer import DATABASE_ID
    notion = _get_notion_client()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _fetch_recent_feedback, notion, DATABASE_ID, 7
    )


def _parse_scoring_response(content, items):
    try:
        data = json.loads(content)
        llm_items = data.get("items", [])
    except (json.JSONDecodeError, TypeError):
        return [_fallback_scored_item(item) for item in items]

    llm_map = {int(e["index"]): e for e in llm_items if "index" in e}
    results = []
    for i, src in enumerate(items):
        e = llm_map.get(i)
        if not e:
            results.append(_fallback_scored_item(src))
            continue
        inc = bool(e.get("include", False))
        results.append(ScoredItem(
            original=src,
            include=inc,
            channel=e.get("channel", "") if inc else "",
            event_cluster=e.get("event_cluster", ""),
            what_happened=e.get("what_happened", "") if inc else "",
            why_it_matters=e.get("why_it_matters", "") if inc else "",
            score_reason=e.get("reason", ""),
        ))
    return results


async def score_items(
    items: list[SourceItem],
    interests: UserInterests,
    model: str = "gpt-5.2",
    max_retries: int = 2,
    feedback: UserFeedback | None = None,
) -> list[ScoredItem]:
    """Curate source items via a single LLM call.

    Pre-filters duplicates in Python, truncates to 150 items if needed,
    then sends everything in one prompt.
    """
    if not items:
        return []

    # Python-level dedup
    items = _pre_filter(items)

    # Truncate to top 150 by platform score if too many
    if len(items) > 150:
        items.sort(key=lambda x: x.score or 0, reverse=True)
        items = items[:150]

    client = _get_client()
    prompt = _build_scoring_prompt(items, interests, feedback)

    content = await _call_with_retry(
        client=client,
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.3,
        max_retries=max_retries,
        response_format={"type": "json_object"},
    )

    if content is not None:
        return _parse_scoring_response(content, items)
    else:
        return [_fallback_scored_item(item) for item in items]


def filter_items(scored, max_items=20):
    return [s for s in scored if s.include][:max_items]
