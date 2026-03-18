"""OpenAI-powered LLM processing for RSS digest items."""

import asyncio
import json
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

from sources.models import ProcessedItem, SourceItem

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


def _get_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client, respecting OPENAI_BASE_URL for local proxies (e.g. EasyCIL)."""
    return AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=60.0,
    )


def _build_batch_prompt(items: list[SourceItem]) -> str:
    """Build the classification/summary prompt for a batch of items."""
    entries = []
    for i, item in enumerate(items):
        entries.append(
            f"[{i}] title: {item.title}\n"
            f"    source: {item.source_name}\n"
            f"    description: {item.description[:300]}\n"
            f"    url: {item.url}"
        )
    items_text = "\n\n".join(entries)

    return (
        "You are an AI/tech news analyst. For each item below, produce a JSON object.\n\n"
        "Return a JSON object with a single key \"items\" whose value is an array. "
        "Each element must have these fields:\n"
        "- index: int (the [i] index from the input)\n"
        "- one_line_summary: string (Chinese, 20-40 characters, concise summary)\n"
        "- category: string (one of: 产品/论文/开源/讨论/新闻)\n"
        "- relevance: string (one of: high/medium/low)\n"
        "- key_insight: string (one English sentence, the most important takeaway)\n"
        "- tags: array of 2-4 short English tags\n\n"
        f"Items:\n\n{items_text}"
    )


def _build_executive_summary_prompt(items: list[ProcessedItem]) -> str:
    """Build the executive summary prompt."""
    entries = []
    for item in items:
        entries.append(
            f"- [{item.category}] {item.one_line_summary or item.original.title} "
            f"(source: {item.original.source_name}, relevance: {item.relevance})\n"
            f"  Insight: {item.key_insight}"
        )
    items_text = "\n".join(entries)

    return (
        "You are a senior AI industry analyst writing a daily briefing.\n\n"
        "Based on the following processed news items, write a 200-400 word executive "
        "summary in Chinese. Your summary must:\n"
        "1. Extract the top 3 trends or themes from today's items\n"
        "2. Identify cross-source connections (patterns that appear across different sources)\n"
        "3. Provide forward-looking insight on what these developments mean\n\n"
        "Write in a professional, concise style suitable for a tech executive audience.\n\n"
        f"Today's items:\n\n{items_text}"
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
    backoff_seconds = [1, 4, 16]  # pre-computed; we only use up to max_retries entries

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


def _fallback_item(item: SourceItem) -> ProcessedItem:
    """Create a minimal ProcessedItem when LLM processing fails."""
    return ProcessedItem(original=item)


async def process_items_batch(
    items: list[SourceItem],
    model: str = "gpt-5.2",
    max_retries: int = 2,
) -> list[ProcessedItem]:
    """Process source items through LLM in batches of 10.

    Each batch is sent as a single prompt asking the model to classify and
    summarize every item. On failure after retries, fallback ProcessedItems
    are created with just the original data.
    """
    if not items:
        return []

    client = _get_client()
    results: list[ProcessedItem] = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start : batch_start + BATCH_SIZE]
        prompt = _build_batch_prompt(batch)

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

                # Build a lookup by index for robustness
                llm_map: dict[int, dict] = {}
                for entry in llm_items:
                    idx = entry.get("index")
                    if idx is not None:
                        llm_map[int(idx)] = entry

                for i, source_item in enumerate(batch):
                    entry = llm_map.get(i)
                    if entry:
                        results.append(
                            ProcessedItem(
                                original=source_item,
                                one_line_summary=entry.get("one_line_summary", ""),
                                category=entry.get("category", ""),
                                relevance=entry.get("relevance", "medium"),
                                key_insight=entry.get("key_insight", ""),
                                tags=entry.get("tags", []),
                            )
                        )
                    else:
                        results.append(_fallback_item(source_item))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.error("Failed to parse LLM response: %s", exc)
                results.extend(_fallback_item(item) for item in batch)
        else:
            # All retries exhausted — fallback for every item in this batch
            results.extend(_fallback_item(item) for item in batch)

    return results


async def generate_executive_summary(
    processed_items: list[ProcessedItem],
    model: str = "gpt-5.2",
) -> str:
    """Generate a 200-400 word Chinese executive summary of high/medium relevance items.

    Returns a generic fallback string if the LLM call fails after retries.
    """
    relevant = [
        item for item in processed_items if item.relevance in ("high", "medium")
    ]
    if not relevant:
        return "今日无高相关性内容。"

    client = _get_client()
    prompt = _build_executive_summary_prompt(relevant)

    content = await _call_with_retry(
        client=client,
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.7,
        max_retries=3,
    )

    if content is not None:
        return content.strip()

    # Fallback summary
    return (
        f"今日共处理 {len(processed_items)} 条信息，"
        f"其中 {len(relevant)} 条为高/中相关性内容。"
        "由于摘要生成失败，请直接查阅各条目详情。"
    )
