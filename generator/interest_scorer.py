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
    source_category: str = ""      # LLM assigns
    source_tier: str = ""          # A/B/C/D/E information tier
    importance: str = "中"         # LLM directly assigns 高/中/低
    one_line_summary: str = ""
    key_insight: str = ""
    tags: list[str] = field(default_factory=list)
    score_reason: str = ""
    event_cluster: str = ""        # Group duplicate coverage of same event


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


def _build_scoring_prompt(
    items: list[SourceItem], interests: UserInterests,
    feedback: UserFeedback | None = None,
) -> str:
    """Build the LLM strategic curation prompt for a batch of items."""
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

    designated_section = ""
    if interests.designated_topic:
        designated_section = (
            f"\n**今日指定课题**: {interests.designated_topic}\n"
            "与指定课题直接相关的内容应强烈倾向入选。\n"
        )

    # Build feedback section from real user behavior
    feedback_section = ""
    if feedback and (feedback.favorited or feedback.ignored):
        fb_lines = []
        if feedback.favorited:
            fb_lines.append("读者最近收藏（认为有价值）的内容：")
            for t in feedback.favorited[:15]:
                fb_lines.append(f"  ✅ {t}")
        if feedback.deep_read:
            fb_lines.append("读者标记深度阅读的内容：")
            for t in feedback.deep_read[:10]:
                fb_lines.append(f"  📖 {t}")
        if feedback.ignored:
            fb_lines.append("读者忽略（不感兴趣）的内容：")
            for t in feedback.ignored[:15]:
                fb_lines.append(f"  ❌ {t}")
        fb_lines.append(
            "从这些真实行为中学习读者的偏好模式，用于校准你的选择。"
        )
        feedback_section = "\n## 读者最近的真实行为（最重要的校准信号）\n" + "\n".join(fb_lines) + "\n"

    return (
        "你是一个服务于 AI 产业战略投资分析师的信息筛选器。\n\n"

        "## 你的读者\n"
        f"**视角**: {interests.perspective}\n"
        f"**长期关注**: {topics_text}\n"
        f"**追踪关键词**: {keywords_text}\n"
        f"{designated_section}\n"
        "这位读者每天只看 10-15 条内容。他需要的不是「发生了什么」，"
        "而是「有什么新的事实或判断，能改变我对某个赛道/公司/技术方向的认知」。\n"
        f"{feedback_section}\n"

        "## 核心判断标准：信息增量\n"
        "对每条内容，依次回答三个问题：\n\n"

        "### Q1: 有没有信息增量？\n"
        "「读完这条，读者能知道一个他昨天还不知道的事实、数据、判断或趋势吗？」\n"
        "- 纯转发、RT、没有附加观点的转推 -> 无增量，排除\n"
        "- 多个源报道同一件事 -> 只保留信息量最大的那条，其余排除\n"
        "- 同一个人连续发的多条推文/同一期播客的多个片段 -> 合并为一条，只保留最有信息量的\n"
        "- 标题党、泛泛而谈、没有具体数据或判断的内容 -> 无增量，排除\n\n"

        "### Q2: 跟 AI 产业战略相关吗？\n"
        "「这条信息会影响读者对一个公司、一个赛道、一个技术方向、或一个商业模式的判断吗？」\n"
        "- 与 AI/科技产业战略无关的内容 -> 排除（哪怕来自关注的源）\n"
        "- 消费者导向的 AI 应用（AI 滤镜、AI 穿搭）-> 排除\n"
        "- 入门教程、how-to 指南 -> 排除\n\n"

        "### Q3: 增量有多深？\n"
        "通过了 Q1 和 Q2 的内容，按深度分三档：\n"
        "- **深**：包含独家数据、一手战略判断、原创分析框架、改变竞争格局的技术突破 -> 优先入选\n"
        "- **中**：有观点但论据不充分，或是重要事件的首发报道 -> 酌情入选\n"
        "- **浅**：只是陈述事实，没有分析 -> 一般不选，除非是重大事件的第一手确认\n\n"

        "## 去重规则（在 Q1 之前先执行）\n"
        "- 同一事件/话题：只保留信息量最大、分析最深的 1 条，用 event_cluster 标注\n"
        "- 同一个人的多条内容：只保留最有信息量的 1-2 条\n"
        "- 同一期播客/访谈的多个片段：只保留 1 条，摘要中涵盖关键要点\n\n"

        "## 目标数量\n"
        "每批精选 3-8 条。宁缺毋滥——如果候选内容质量普遍不高，选 1-2 条也行。"
        "不要为了凑数降低标准。\n\n"

        "## 输出格式\n"
        "返回 JSON，key 为 \"items\"，value 为数组。每个元素：\n"
        "- index: int\n"
        "- include: boolean\n"
        "- event_cluster: string（同一事件标注事件名，否则空字符串）\n"
        "- topic: string（简洁话题标签）\n"
        "- importance: string（高/中/低）\n"
        "- one_line_summary: string（中文，50-100字，必须包含「新增量是什么」）\n"
        "- key_insight: string（一句英文，这条内容的核心新信息）\n"
        "- score_reason: string（1-2句，回答「为什么选/不选」，必须引用 Q1/Q2/Q3 的判断）\n\n"

        f"候选内容（{len(items)}条）：\n\n{items_text}"
    )


def _fallback_scored_item(item: SourceItem) -> ScoredItem:
    """Create a minimal ScoredItem when LLM scoring fails."""
    return ScoredItem(original=item, score=3, include=False, topic="未分类", importance="低")


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


async def score_items(
    items: list[SourceItem],
    interests: UserInterests,
    model: str = "gpt-5.2",
    max_retries: int = 2,
    feedback: UserFeedback | None = None,
) -> list[ScoredItem]:
    """Curate source items via LLM with information-tier classification.

    The LLM acts as a strategic curator — classifying by information tier
    (A=一手/B=深度分析/C=二手/D=社区/E=教程), clustering duplicate events,
    and selecting based on strategic value rather than keyword matching.
    """
    if not items:
        return []

    client = _get_client()
    results: list[ScoredItem] = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start: batch_start + BATCH_SIZE]
        prompt = _build_scoring_prompt(batch, interests, feedback)

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
                        importance = entry.get("importance", "中")
                        if importance not in ("高", "中", "低"):
                            importance = "中"

                        # Derive score from importance for backwards compat
                        imp_scores = {"高": 9, "中": 5, "低": 2}
                        raw_score = imp_scores.get(importance, 5)

                        results.append(
                            ScoredItem(
                                original=source_item,
                                score=raw_score,
                                include=bool(entry.get("include", False)),
                                topic=entry.get("topic", ""),
                                event_cluster=entry.get("event_cluster", ""),
                                importance=importance,
                                one_line_summary=entry.get("one_line_summary", ""),
                                key_insight=entry.get("key_insight", ""),
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
