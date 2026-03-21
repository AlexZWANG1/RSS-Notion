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

BATCH_SIZE = 15

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
    score: int = 5
    include: bool = False          # LLM decides: should this be in today's digest?
    topic: str = ""                # Free-form, LLM assigns
    content_type: str = ""         # Free-form, LLM assigns
    source_category: str = ""      # LLM assigns: 科技媒体/AI技术社区/论文与评审/社交社区视频/官方一手/个人分析师/数据榜单基准/投资机构报告/独立研究机构
    importance: str = "中"         # LLM directly assigns 高/中/低
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


def _build_scoring_prompt(
    items: list[SourceItem], interests: UserInterests
) -> str:
    """Build the LLM editorial curation prompt for a batch of items."""
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
    keywords_text = ", ".join(interests.keywords[:50])
    research_text = ", ".join(interests.research_titles[:30])

    designated_section = ""
    if interests.designated_topic:
        designated_section = (
            f"\n**今日指定课题**: {interests.designated_topic}\n"
            "Items directly related to the designated topic should be strongly favored for inclusion.\n"
        )

    return (
        "You are an editorial curator for a personalized AI daily digest. "
        "Your job is to decide which items are worth the reader's time today.\n\n"

        "## Reader Profile\n"
        f"**Perspective**: {interests.perspective}\n"
        f"**Long-term interests**: {topics_text}\n"
        f"**Keywords they track**: {keywords_text}\n"
        f"**Existing research topics**: {research_text}\n"
        f"{designated_section}\n"

        "## Your editorial criteria\n"
        "Think like a thoughtful human editor, not a keyword matcher:\n"
        "- The reader is a **product strategist and investor**, not a developer or consumer. "
        "They care about: industry dynamics, business model shifts, competitive moats, "
        "product strategy, funding signals, and platform plays — NOT tutorials, how-to guides, "
        "or consumer lifestyle content.\n"
        "- **Include** items with genuine strategic signal: new product launches with market implications, "
        "founder/CEO first-hand insights, industry inflection points, open-source projects that shift "
        "competitive dynamics, investment trends, and deep analyses with original thinking.\n"
        "- **Exclude** beginner tutorials, 'how to use X' guides, consumer content (fashion/lifestyle/tips), "
        "routine paper summaries with no practical relevance, and hype without substance.\n"
        "- Cast a reasonably wide net — include 6-10 items per batch. When in doubt about strategic "
        "relevance, include it. But don't include obvious noise.\n\n"

        "## Output format\n"
        "Return a JSON object with a single key \"items\" whose value is an array. "
        "Each element must have:\n"
        "- index: int (the [i] index from input)\n"
        "- include: boolean (your editorial decision — should this be in today's digest?)\n"
        "- score: int (1-10, your confidence in the recommendation)\n"
        "- topic: string (assign a concise topic label that fits the content — "
        "use the reader's interest topics when appropriate, or create a fitting label)\n"
        "- content_type: string (classify freely, e.g. 新闻/深度分析/技术报告/博客/开源项目/论文/产品发布/行业动态/观点 — whatever fits best)\n"
        "- source_category: string (classify the information source type. "
        "MUST be one of: 科技媒体, AI技术社区, 论文与评审, 社交/社区/视频, 官方一手, 个人分析师, "
        "数据/榜单/基准, 投资机构报告, 独立研究机构. "
        "Judge by the actual source nature: e.g. arXiv→论文与评审, HN/Reddit→AI技术社区, "
        "company blogs→官方一手, YouTube→社交/社区/视频, GitHub→数据/榜单/基准)\n"
        "- importance: string (高/中/低 — your editorial judgment of how important this is to the reader)\n"
        "- one_line_summary: string (Chinese, 50-100 characters — not just a headline, "
        "include essential background context and the core viewpoint/finding. "
        "Example: '谷歌发布Gemini 2.0，首次支持原生多模态输出和AI Agent调用工具，标志着从信息检索向任务执行的范式转变')\n"
        "- key_insight: string (one English sentence, the most important takeaway)\n"
        "- tags: array of 2-4 short tags\n"
        "- score_reason: string (1-2 sentences explaining your editorial decision)\n\n"

        f"Items:\n\n{items_text}"
    )


def _fallback_scored_item(item: SourceItem) -> ScoredItem:
    """Create a minimal ScoredItem when LLM scoring fails."""
    return ScoredItem(original=item, score=3, include=False, topic="未分类", importance="低")


async def score_items(
    items: list[SourceItem],
    interests: UserInterests,
    model: str = "gpt-5.2",
    max_retries: int = 2,
) -> list[ScoredItem]:
    """Curate source items against user interests via LLM.

    The LLM acts as an editorial curator — it decides include/exclude,
    assigns topics and content types freely, and judges importance directly.
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
                        # LLM assigns all fields freely — no validation against hardcoded lists
                        importance = entry.get("importance", "中")
                        if importance not in ("高", "中", "低"):
                            importance = "中"

                        results.append(
                            ScoredItem(
                                original=source_item,
                                score=raw_score,
                                include=bool(entry.get("include", False)),
                                topic=entry.get("topic", ""),
                                content_type=entry.get("content_type", ""),
                                source_category=entry.get("source_category", ""),
                                importance=importance,
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
    max_items: int = 20,
) -> list[ScoredItem]:
    """Filter items based on LLM's editorial decision.

    Primary filter: LLM's include=True decision.
    Fallback: if LLM includes too few (<5), also add items with score >= threshold.
    Safety cap: max_items prevents runaway output.
    """
    # Primary: LLM's editorial picks
    included = [s for s in scored if s.include]

    # Fallback: if LLM was still too conservative, add high-scoring items
    if len(included) < 5:
        above_threshold = [s for s in scored if not s.include and s.score >= threshold]
        above_threshold.sort(key=lambda s: s.score, reverse=True)
        included.extend(above_threshold)

    # Sort by score descending, cap at max
    included.sort(key=lambda s: s.score, reverse=True)
    return included[:max_items]
