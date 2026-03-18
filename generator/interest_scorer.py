"""Score source items against user interests loaded from Notion config page."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

from sources.models import SourceItem

logger = logging.getLogger(__name__)

BATCH_SIZE = 15

CONFIG_PAGE_ID = "32516831-83e6-8100-b28f-f60937b0d472"
RESEARCH_DB_ID = "2fe16831-83e6-805c-a095-000bab8d1eca"

VALID_TOPICS = [
    "Agent Infra",
    "AI应用层",
    "MCP",
    "Tool Use",
    "大模型推理",
    "Agent应用",
    "Agent安全",
    "云原生",
    "产品战略",
    "投资",
    "商业模式",
]

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


VALID_CONTENT_TYPES = ["新闻", "深度分析", "技术报告", "博客/视频", "开源项目"]


@dataclass
class ScoredItem:
    """A source item with interest-relevance scoring."""
    original: SourceItem
    score: int = 5
    topic: str = ""
    content_type: str = "新闻"  # 新闻/深度分析/技术报告/博客视频/开源项目
    importance: str = "中"
    one_line_summary: str = ""
    key_insight: str = ""
    tags: list[str] = field(default_factory=list)
    score_reason: str = ""


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
    """Walk page blocks and map section headings to their body text.

    Expects a layout like:
        ## 筛选视角
        产品人、投资人、创业者
        ## 长期关注课题
        - topic A
        - topic B
        ...
    """
    sections: dict[str, str] = {}
    current_heading: Optional[str] = None
    current_lines: list[str] = []

    for block in blocks:
        btype = block.get("type", "")

        # Heading blocks
        if btype in ("heading_1", "heading_2", "heading_3"):
            # Save previous section
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines).strip()
            heading_data = block.get(btype, {})
            current_heading = _extract_rich_text(heading_data.get("rich_text", []))
            current_lines = []
            continue

        # Content blocks (paragraph, bulleted list, numbered list, etc.)
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

    # Flush last section
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
                # Try common title property names
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_user_interests(config: dict | None = None) -> UserInterests:
    """Load user interest configuration from Notion config page.

    Falls back to default AI/tech keywords if NOTION_TOKEN is not set.
    """
    if not os.environ.get("NOTION_TOKEN"):
        logger.info("NOTION_TOKEN not set — using default interests")
        return UserInterests()

    # Allow overriding IDs from config.json
    notion_cfg = (config or {}).get("notion", {})
    page_id = notion_cfg.get("config_page_id", CONFIG_PAGE_ID)
    research_db = notion_cfg.get("research_database_data_source", "")
    # Strip "collection://" prefix if present
    research_id = research_db.replace("collection://", "") if research_db else RESEARCH_DB_ID

    loop = asyncio.get_running_loop()

    try:
        notion = _get_notion_client()

        # Fetch config page blocks and research titles in parallel via executor
        sections_future = loop.run_in_executor(
            None, _fetch_config_page, notion, page_id
        )
        titles_future = loop.run_in_executor(
            None, _fetch_research_titles, notion, research_id
        )

        sections, research_titles = await asyncio.gather(
            sections_future, titles_future
        )

        # Parse sections
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


def _build_scoring_prompt(
    items: list[SourceItem], interests: UserInterests
) -> str:
    """Build the LLM scoring prompt for a batch of items."""
    entries = []
    for i, item in enumerate(items):
        entries.append(
            f"[{i}] title: {item.title}\n"
            f"    source: {item.source_name}\n"
            f"    description: {item.description[:400]}\n"
            f"    url: {item.url}"
        )
    items_text = "\n\n".join(entries)

    topics_text = ", ".join(interests.topics)
    keywords_text = ", ".join(interests.keywords[:50])  # cap to avoid prompt bloat
    research_text = ", ".join(interests.research_titles[:30])

    valid_topics_text = " / ".join(VALID_TOPICS)
    valid_types_text = " / ".join(VALID_CONTENT_TYPES)

    designated_section = ""
    if interests.designated_topic:
        designated_section = (
            f"\n**今日指定课题**: {interests.designated_topic}\n"
            "Items directly related to the designated topic should receive a score boost (+2, capped at 10).\n"
        )

    return (
        "You are an AI content scoring assistant. Score each item's relevance to the "
        "user's interests.\n\n"
        f"**User perspective**: {interests.perspective}\n"
        f"**Long-term topics**: {topics_text}\n"
        f"**Keywords**: {keywords_text}\n"
        f"**Existing research topics**: {research_text}\n"
        f"{designated_section}\n"
        "## Scoring rubric\n"
        "- 10 = directly hits a core topic or designated topic\n"
        "- 7-9 = highly relevant to user's interests\n"
        "- 4-6 = somewhat relevant, tangentially related\n"
        "- 1-3 = not relevant to user's interests\n\n"
        "## Output format\n"
        "Return a JSON object with a single key \"items\" whose value is an array. "
        "Each element must have:\n"
        "- index: int (the [i] index from input)\n"
        "- score: int (1-10)\n"
        f"- topic: string (map to ONE of: {valid_topics_text})\n"
        f"- content_type: string (map to ONE of: {valid_types_text})\n"
        "- one_line_summary: string (Chinese, 20-40 characters)\n"
        "- key_insight: string (one English sentence, the most important takeaway)\n"
        "- tags: array of 2-4 short tags\n"
        "- score_reason: string (brief explanation of the score, 1-2 sentences)\n\n"
        f"Items:\n\n{items_text}"
    )


def _importance_from_score(score: int) -> str:
    """Map numeric score to importance label."""
    if score >= 9:
        return "高"
    elif score >= 7:
        return "中"
    return "低"


def _fallback_scored_item(item: SourceItem) -> ScoredItem:
    """Create a minimal ScoredItem when LLM scoring fails."""
    return ScoredItem(original=item, score=5, topic="AI应用层", importance="低")


async def score_items(
    items: list[SourceItem],
    interests: UserInterests,
    model: str = "gpt-5.2",
    max_retries: int = 2,
) -> list[ScoredItem]:
    """Score source items against user interests via LLM.

    Items are sent in batches of 15. Returns a list of ScoredItem with
    relevance scores, topic mapping, summaries, and insights.
    """
    if not items:
        return []

    client = _get_client()
    results: list[ScoredItem] = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start: batch_start + BATCH_SIZE]
        prompt = _build_scoring_prompt(batch, interests)

        content = await _call_with_retry(
            client=client,
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.3,
            max_retries=max_retries,
            response_format={"type": "json_object"},
        )

        if content is not None:
            try:
                data = json.loads(content)
                llm_items = data.get("items", [])

                llm_map: dict[int, dict] = {}
                for entry in llm_items:
                    idx = entry.get("index")
                    if idx is not None:
                        llm_map[int(idx)] = entry

                for i, source_item in enumerate(batch):
                    entry = llm_map.get(i)
                    if entry:
                        raw_score = max(1, min(10, int(entry.get("score", 5))))
                        topic = entry.get("topic", "AI应用层")
                        if topic not in VALID_TOPICS:
                            topic = "AI应用层"
                        ctype = entry.get("content_type", "新闻")
                        if ctype not in VALID_CONTENT_TYPES:
                            ctype = "新闻"

                        results.append(
                            ScoredItem(
                                original=source_item,
                                score=raw_score,
                                topic=topic,
                                content_type=ctype,
                                importance=_importance_from_score(raw_score),
                                one_line_summary=entry.get("one_line_summary", ""),
                                key_insight=entry.get("key_insight", ""),
                                tags=entry.get("tags", []),
                                score_reason=entry.get("score_reason", ""),
                            )
                        )
                    else:
                        results.append(_fallback_scored_item(source_item))

            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.error("Failed to parse scoring response: %s", exc)
                results.extend(_fallback_scored_item(item) for item in batch)
        else:
            results.extend(_fallback_scored_item(item) for item in batch)

    return results


def filter_items(
    scored: list[ScoredItem],
    threshold: int = 7,
    max_items: int = 15,
) -> list[ScoredItem]:
    """Filter scored items by threshold and cap at max_items.

    Returns items sorted by score descending.
    """
    above = [s for s in scored if s.score >= threshold]
    above.sort(key=lambda s: s.score, reverse=True)
    return above[:max_items]
