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
# OpenAI client
# ---------------------------------------------------------------------------

def _get_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client with Langfuse observability if configured."""
    client = AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=300.0,
    )

    # Wrap with Langfuse if configured
    if os.environ.get("LANGFUSE_SECRET_KEY"):
        try:
            from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI
            client = LangfuseAsyncOpenAI(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                base_url=os.environ.get("OPENAI_BASE_URL"),
                timeout=300.0,
            )
            logger.info("Langfuse observability enabled")
        except ImportError:
            logger.debug("langfuse not installed, skipping observability")
        except Exception as e:
            logger.warning(f"Langfuse init failed: {e}")

    return client


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
                "timeout": 300.0,
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
    import httpx

    titles: list[str] = []
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return titles

    try:
        cursor = None
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor

            resp = httpx.post(
                f"https://api.notion.com/v1/databases/{database_id}/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()

            for page in data.get("results", []):
                props = page.get("properties", {})
                for key in ("Name", "名称", "title", "Title"):
                    title_prop = props.get(key)
                    if title_prop and title_prop.get("type") == "title":
                        text = _extract_rich_text(title_prop.get("title", []))
                        if text:
                            titles.append(text)
                        break

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    except Exception as exc:
        logger.warning("Failed to fetch research database titles: %s", exc)

    return titles


# ---------------------------------------------------------------------------
# Web Clipper integration
# ---------------------------------------------------------------------------

def _parse_clipper_results(results: list) -> str:
    """Parse Notion query results from Web Clipper into text for prompt."""
    if not results:
        return ""
    lines = []
    for page in results:
        props = page.get("properties", {})
        title_arr = props.get("标题", {}).get("title", [])
        title = title_arr[0]["plain_text"] if title_arr else "Untitled"
        url = props.get("userDefined:URL", {}).get("url", "")
        tags = [t["name"] for t in props.get("标签", {}).get("multi_select", [])]
        tag_str = ", ".join(tags) if tags else ""
        date_str = props.get("摘取时间", {}).get("created_time", "")[:10]

        line = f"- {title}"
        if tag_str:
            line += f" [{tag_str}]"
        if date_str:
            line += f" ({date_str})"
        lines.append(line)
    return "\n".join(lines)


async def load_clipper_items(config: dict) -> str:
    """Query Web Clipper database for recent 14 days of clippings. Returns formatted text."""
    import httpx

    db_id = (config or {}).get("notion", {}).get("clipper_database_id", "")
    if not db_id:
        logger.warning("No clipper_database_id in config, skipping Web Clipper")
        return ""

    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return ""

    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: httpx.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json={
                    "filter": {"property": "摘取时间", "created_time": {"on_or_after": cutoff}},
                    "sorts": [{"property": "摘取时间", "direction": "descending"}],
                },
                timeout=20.0,
            )
        )
        results = response.json().get("results", [])
        text = _parse_clipper_results(results)
        if text:
            logger.info(f"Loaded {len(results)} Web Clipper items")
        return text
    except Exception as e:
        logger.warning(f"Failed to query Web Clipper: {e}")
        return ""


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


def _build_scoring_prompt(items: list, clipper_text: str, interests_text: str, source_stats: str = "") -> tuple[str, str]:
    """Build system + user prompt for tiered scoring.

    Returns (system_prompt, user_prompt) tuple.
    """
    system_prompt = """你是一位信息编辑部主编，负责为用户从海量信息中筛选出最有价值的内容。你需要完成三件事：筛选分层、撰写日报摘要、撰写编辑反思日志。

## 任务一：筛选分层
1. 先阅读用户近期主动收藏的内容，推断其兴趣方向和偏好
2. 对待筛选文章进行事件聚类：多篇报道同一事件的合并为一个事件，选择最佳来源
3. 按三个层级筛选输出

### 分层标准
- 📰 headline（2-3 条）：改变行业格局的重大事件，或 3+ 来源报道的多源热点。给出 200-300 字深度分析。
- 🔍 noteworthy（4-6 条）：有信息增量，值得花 2 分钟了解。给出 80-100 字摘要 + 一句话洞察。
- ⚡ glance（8-12 条）：知道有这事就行。一句话概括。确保覆盖各来源分类。

### 来源分类（channel）
每个 related_source 必须标注 channel，只能从以下选项中选：
- "一手/官方" — 官方发布、一手消息（OpenAI、Google、Anthropic、DeepMind、Microsoft Research、Apple ML 等官方博客）
- "深度研究" — 深度分析、研究报告、独立思考（Ars Technica、WIRED、MIT Tech Review、36氪、量子位、InfoQ、Simon Willison、Semafor 等）
- "长内容/播客" — 播客、长视频、长文（No Priors、All-In、Lex Fridman、Dwarkesh Patel、Fireship、Two Minute Papers 等 YouTube 频道，以及 Stratechery、Latent.Space 等长文）
- "社交/社区/Twitter" — Twitter/X、Reddit、社区讨论
- "开源/技术/论文" — GitHub 项目、arXiv 论文、技术文章

### 筛选原则
- 信息增量原则：「读完这条，会改变你对 AI 领域某件事的认知吗？」
- 不要只推与收藏相似的内容 — 保留 20-30% 的意外发现空间
- 事件去重：同一事件只保留最佳来源（最详细/最权威）
- **来源多样性**：最终精选结果中，5 个 channel 分类每个至少出现 1 次。如果某个分类确实没有值得入选的内容，在 run_report 中说明原因。特别注意不要忽略 YouTube/播客（长内容/播客）和官方博客（一手/官方）的内容。

## 任务二：日报摘要
写一段 50-100 字的今日总结，概括今天信息流的主线和关键信号。

## 任务三：运行报告
根据你收到的全量文章和数据源统计信息，撰写一份完整的系统运行报告。报告需要包含以下部分：

1. **处理概览**：一句话总结今天的处理情况（抓取总数、精选总数、通过率）
2. **数据源明细表**：逐个列出每个数据源的名称、抓取条数、耗时、状态，以及该源下的所有文章标题（每篇一行）。格式示例：
   【Folo】60条 | 2765ms | ✅
     · 文章标题1
     · 文章标题2
3. **筛选决策说明**：为什么选了这些、淘汰了哪些、有哪些边界案例
4. **事件聚类说明**：哪些文章被合并了、为什么选择某个作为最佳来源
5. **信号与趋势**：从今天全量信息中发现的趋势、异常、值得持续关注的信号
6. **自我反思**：这次筛选可能漏掉了什么？信息源有什么偏差？下次可以改进什么？

## 输出格式
严格输出 JSON，不要有其他内容：
{
  "headline": [{"event_title": "...", "source_count": N, "best_source_url": "...", "best_source_name": "...", "analysis": "200-300字", "related_sources": [{"title": "原文标题", "url": "...", "source_name": "来源名", "channel": "来源分类", "one_liner": "这篇文章的一句话摘要"}]}],
  "noteworthy": [{"event_title": "...", "source_count": N, "best_source_url": "...", "best_source_name": "...", "summary": "80-100字", "insight": "一句话", "related_sources": [{"title": "原文标题", "url": "...", "source_name": "来源名", "channel": "来源分类", "one_liner": "这篇文章的一句话摘要"}]}],
  "glance": [{"title": "原文标题", "url": "...", "source_name": "来源名", "channel": "来源分类", "one_liner": "一句话摘要"}],
  "daily_summary": "50-100字今日总结",
  "run_report": "完整运行报告（包含上述6个部分，1000-1500字，用 Markdown 格式）",
  "events_total": N,
  "selected_total": N
}

注意：
- related_sources 必须列出该事件涉及的所有原始文章（标题、URL、来源名、channel），不要省略
- glance 也要保留原文标题、来源名和 channel
- channel 必须严格使用上面 5 个选项之一
- run_report 是完整的运行日志，要写得详细，这是给编辑团队内部审阅的，必须包含数据源明细表（每个源的每篇文章标题都要列出）"""

    # User prompt
    parts = []
    if clipper_text:
        parts.append(f"## 用户近期主动收藏（据此推断兴趣方向）\n{clipper_text}")
    elif interests_text:
        parts.append(f"## 用户兴趣描述\n{interests_text}")

    if source_stats:
        parts.append(f"\n## 数据源抓取统计（用于运行报告）\n{source_stats}")

    parts.append(f"\n## 待筛选文章（共 {len(items)} 篇）\n")

    # Token safety valve: estimate total size, downgrade if too large
    max_chars_per_item = 800
    estimated_chars = sum(len((item.description or "")[:max_chars_per_item]) for item in items)
    estimated_tokens = int(estimated_chars * 1.7)
    if estimated_tokens > 300_000:
        max_chars_per_item = 500
        logger.warning(f"Token budget tight (~{estimated_tokens}), reducing to {max_chars_per_item} chars/item")

    for i, item in enumerate(items):
        content = (item.description or "")[:max_chars_per_item]
        parts.append(f"### [{i+1}] {item.title}\n来源: {item.source_name} | URL: {item.url}\n{content}\n")

    user_prompt = "\n".join(parts)
    return system_prompt, user_prompt


def _parse_tiered_response(raw: str) -> dict | None:
    """Parse LLM JSON response into tiered structure. Returns None on failure."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)

        for key in ("headline", "noteworthy", "glance", "daily_summary", "run_report"):
            if key not in data:
                logger.warning(f"Missing key in LLM response: {key}")
                # run_report is important but not blocking
                if key != "run_report":
                    return None
                data["run_report"] = ""

        return data
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to parse tiered response: {e}")
        return None


async def score_items(
    items: list[SourceItem],
    config: dict,
    interests: "UserInterests | None" = None,
    clipper_text: str = "",
    source_stats: str = "",
) -> dict | None:
    """Score and tier items with single LLM call.

    Returns tiered dict with headline/noteworthy/glance/daily_summary,
    or None on failure.
    """
    if not items:
        logger.warning("No items to score")
        return None

    items = _pre_filter(items)

    if len(items) > 200:
        items.sort(key=lambda x: x.score or 0, reverse=True)
        items = items[:200]

    # Build interests text as fallback when no clipper data
    interests_text = ""
    if interests:
        parts = []
        if interests.perspective:
            parts.append(f"视角: {interests.perspective}")
        if interests.topics:
            parts.append(f"关注话题: {', '.join(interests.topics)}")
        if interests.keywords:
            parts.append(f"关键词: {', '.join(interests.keywords)}")
        interests_text = "\n".join(parts)

    system_prompt, user_prompt = _build_scoring_prompt(items, clipper_text, interests_text, source_stats)

    logger.info(f"Scoring {len(items)} items (clipper signal: {'yes' if clipper_text else 'no'})")

    client = _get_client()
    model = config.get("pipeline", {}).get("llm", {}).get("processing_model", "gpt-5.4-mini")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response = await _call_with_retry(client, messages, model, temperature=0.3, max_retries=2)

    if not response:
        logger.error("LLM scoring call failed")
        return None

    result = _parse_tiered_response(response)
    if not result:
        logger.warning("First parse failed, retrying LLM call")
        response = await _call_with_retry(client, messages, model, temperature=0.2, max_retries=1)
        if response:
            result = _parse_tiered_response(response)

    return result
