"""Shared Jina Reader utility for fetching article body text."""

import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"
DEFAULT_TIMEOUT = 10  # seconds
DEFAULT_MAX_CHARS = 800


async def fetch_content(url: str, max_chars: int = DEFAULT_MAX_CHARS, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch article body text via Jina Reader. Returns empty string on failure."""
    try:
        jina_url = f"{JINA_PREFIX}{url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(jina_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return text[:max_chars]
                logger.warning(f"Jina Reader returned {resp.status} for {url}")
                return ""
    except (TimeoutError, aiohttp.ClientError, Exception) as e:
        logger.warning(f"Content fetch failed for {url}: {e}")
        return ""


async def fetch_content_batch(urls: list[str], max_chars: int = DEFAULT_MAX_CHARS, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """Fetch multiple URLs concurrently. Returns list of body texts (empty string on failure)."""
    tasks = [fetch_content(url, max_chars, timeout) for url in urls]
    return await asyncio.gather(*tasks)
