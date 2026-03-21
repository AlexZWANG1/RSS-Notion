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
        "你是一个面向 AI 产业战略决策者的信息策展人。\n\n"

        "## 读者画像\n"
        f"**视角**: {interests.perspective}\n"
        f"**长期关注**: {topics_text}\n"
        f"**追踪关键词**: {keywords_text}\n"
        f"{designated_section}\n"
        "这位读者是产品策略师和投资者。他不需要知道「发生了什么」——"
        "他需要知道「这意味着什么」和「谁在一手推动这件事」。\n"
        f"{feedback_section}\n"

        "## 信息层级（你的核心判断维度）\n"
        "对每条内容判断其信息层级：\n"
        "- **A. 一手信息**：创始人/CEO亲自写的、官方公告、原始研究报告、投资人自己的判断\n"
        "- **B. 深度分析**：有原创观点的分析文章（不是转述别人的观点）\n"
        "- **C. 二手报道**：媒体对事件的报道、总结、转述\n"
        "- **D. 社区讨论**：Reddit/HN/小红书上的讨论帖、问答\n"
        "- **E. 教程/工具**：how-to、入门指南、工具推荐、消费者内容\n\n"

        "## 选择规则\n"
        "1. **事件去重**：多个源报道同一件事时，只保留信息层级最高的来源，"
        "用 event_cluster 标注同一事件\n"
        "2. **按层级选择**：\n"
        "   - A 类（一手信息）→ 几乎全选\n"
        "   - B 类（深度分析）→ 选最好的几篇\n"
        "   - C 类（二手报道）→ 同一事件只留一条\n"
        "   - D 类（社区讨论）→ 只有真正有独特见解的才选\n"
        "   - E 类（教程/工具/消费者内容）→ 基本不选\n"
        "3. **优先级排序**：\n"
        "   P1: 行业趋势的深度分析（有数据、有判断、有原创观点）\n"
        "   P2: 创始人/投资人的一手表态或判断\n"
        "   P3: 头部公司的重要产品/战略动作（选官方一手源）\n"
        "   P4: 改变竞争格局的技术突破或开源项目\n\n"

        "## 强排除规则\n"
        "直接排除，不管看起来多'AI相关'：\n"
        "- 入门教程、how-to指南、'如何使用X'类内容\n"
        "- 没有原创观点的纯新闻转述（除非是首发重大消息）\n"
        "- 消费者导向内容（AI穿搭、AI滤镜、AI手机功能）\n"
        "- 泛泛而谈没有数据或具体判断的水文\n\n"

        "## 输出格式\n"
        "返回 JSON，key 为 \"items\"，value 为数组。每个元素：\n"
        "- index: int（输入的 [i] 索引）\n"
        "- include: boolean（是否入选今日日报）\n"
        "- source_tier: string（A/B/C/D/E 信息层级）\n"
        "- event_cluster: string（如果与其他条目讲同一件事，标注事件名，否则空字符串）\n"
        "- topic: string（简洁的话题标签）\n"
        "- importance: string（高/中/低）\n"
        "- one_line_summary: string（中文，50-100字，包含背景和核心观点。"
        "例：'谷歌发布Gemini 2.0，首次支持原生多模态输出和AI Agent调用工具，"
        "标志着从信息检索向任务执行的范式转变'）\n"
        "- key_insight: string（一句英文核心洞察）\n"
        "- score_reason: string（1-2句，为什么选/不选这条）\n\n"

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

                        # Derive score from source_tier for backwards compat
                        tier = entry.get("source_tier", "C")
                        tier_scores = {"A": 9, "B": 7, "C": 5, "D": 3, "E": 1}
                        raw_score = tier_scores.get(tier, 5)

                        results.append(
                            ScoredItem(
                                original=source_item,
                                score=raw_score,
                                include=bool(entry.get("include", False)),
                                topic=entry.get("topic", ""),
                                source_tier=tier,
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
