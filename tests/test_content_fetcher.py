import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sources.content_fetcher import fetch_content, fetch_content_batch


@pytest.mark.asyncio
async def test_fetch_content_returns_text():
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value="Article body text here " * 50)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_ctx)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await fetch_content("https://example.com/article")
        assert len(result) > 0
        assert len(result) <= 800  # truncated to max_chars


@pytest.mark.asyncio
async def test_fetch_content_timeout_returns_empty():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(side_effect=TimeoutError)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await fetch_content("https://example.com/slow")
        assert result == ""


@pytest.mark.asyncio
async def test_fetch_content_batch_concurrent():
    urls = ["https://example.com/1", "https://example.com/2"]
    with patch("sources.content_fetcher.fetch_content", new_callable=AsyncMock, return_value="body"):
        results = await fetch_content_batch(urls)
        assert len(results) == 2
        assert all(r == "body" for r in results)
