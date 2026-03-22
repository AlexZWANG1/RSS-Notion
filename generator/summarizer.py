"""OpenAI-powered LLM processing for RSS digest items."""

import asyncio
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _get_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client, respecting OPENAI_BASE_URL for local proxies (e.g. EasyCIL)."""
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


async def generate_executive_summary(selected_items, model="gpt-5.4-mini"):
    """Generate trend observations from selected items."""
    if not selected_items:
        return ""

    client = _get_client()
    items_text = "\n".join(
        f"- [{getattr(s, 'channel', '')}] {s.original.title}\n"
        f"  {getattr(s, 'what_happened', '') or getattr(s, 'one_line_summary', '')}\n"
        f"  → {getattr(s, 'why_it_matters', '') or getattr(s, 'key_insight', '')}"
        for s in selected_items
    )

    prompt = (
        f"基于今天入选的 {len(selected_items)} 条内容写趋势观察。\n\n"
        "不要罗列各条——读者已经逐条看过了。\n"
        "找跨条目的关联：多条是否指向同一趋势？有就说清楚；没有就挑最重要的1件事展开。\n"
        "100-200字，不要废话，直接进入内容。\n\n"
        f"{items_text}"
    )

    content = await _call_with_retry(
        client=client,
        messages=[{"role": "user", "content": prompt}],
        model=model, temperature=0.7, max_retries=2,
    )
    return content.strip() if content else ""
