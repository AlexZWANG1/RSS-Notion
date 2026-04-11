"""Shared Jina Reader utility for fetching article body text."""

import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"
DEFAULT_TIMEOUT = 10  # seconds
DEFAULT_MAX_CHARS = 800
MAX_CONCURRENCY = 5
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0  # seconds

# Module-level semaphore — shared across all callers within one event loop
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    return _semaphore


async def fetch_content(url: str, max_chars: int = DEFAULT_MAX_CHARS, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch article body text via Jina Reader. Returns empty string on failure."""
    sem = _get_semaphore()
    jina_url = f"{JINA_PREFIX}{url}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with sem:
                async with aiohttp.ClientSession() as session:
                    async with session.get(jina_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            return text[:max_chars]
                        if resp.status == 429 and attempt < MAX_RETRIES:
                            delay = RETRY_BASE_DELAY * (2 ** attempt)
                            logger.info(f"Jina 429 for {url}, retrying in {delay:.0f}s ({attempt+1}/{MAX_RETRIES})")
                            await asyncio.sleep(delay)
                            continue
                        logger.warning(f"Jina Reader returned {resp.status} for {url}")
                        return ""
        except (TimeoutError, aiohttp.ClientError, Exception) as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                continue
            logger.warning(f"Content fetch failed for {url}: {e}")
            return ""
    return ""


async def fetch_content_batch(urls: list[str], max_chars: int = DEFAULT_MAX_CHARS, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """Fetch multiple URLs with concurrency limit. Returns list of body texts (empty string on failure)."""
    tasks = [fetch_content(url, max_chars, timeout) for url in urls]
    return await asyncio.gather(*tasks)
