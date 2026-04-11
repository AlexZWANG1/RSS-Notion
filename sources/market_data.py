"""Market data source — fetches daily quotes for M7 + major indices via Alpha Vantage."""

import asyncio
import logging
import os
from datetime import datetime

import aiohttp

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

# Default tickers
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
DEFAULT_INDICES = ["SPY", "QQQ"]
# VIX needs special handling — Alpha Vantage doesn't support ^VIX directly,
# we use the CBOE VIX ETF (VIXY) or skip if unavailable.

_TICKER_NAMES = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta",
    "TSLA": "Tesla",
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
}


class MarketDataSource(BaseSource):
    """Fetch daily stock quotes from Alpha Vantage."""

    name = "Market Data"
    icon = "📈"

    def __init__(self, config: dict):
        super().__init__(config)
        self.tickers: list[str] = config.get("tickers", DEFAULT_TICKERS)
        self.indices: list[str] = config.get("indices", DEFAULT_INDICES)
        self.api_key: str = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        self.alert_threshold_pct: float = config.get("alert_threshold_pct", 5.0)

    async def _fetch(self) -> list[SourceItem]:
        if not self.api_key:
            logger.warning("[Market Data] ALPHA_VANTAGE_API_KEY not set, skipping")
            return []

        all_symbols = self.tickers + self.indices
        items: list[SourceItem] = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            for i, symbol in enumerate(all_symbols):
                if i > 0:
                    await asyncio.sleep(13)  # Free tier: 5 calls/min
                quote = await self._fetch_quote(session, symbol)
                if quote:
                    items.append(quote)

        return items

    async def _fetch_quote(
        self, session: aiohttp.ClientSession, symbol: str
    ) -> SourceItem | None:
        """Fetch a single stock quote via Alpha Vantage GLOBAL_QUOTE endpoint."""
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={self.api_key}"
        )
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("[Market Data] %s: HTTP %d", symbol, resp.status)
                    return None
                data = await resp.json()

            gq = data.get("Global Quote", {})
            if not gq or not gq.get("05. price"):
                note = data.get("Note", data.get("Information", ""))
                if note:
                    logger.warning("[Market Data] API limit: %s", note[:100])
                return None

            price = float(gq["05. price"])
            change = float(gq["09. change"])
            change_pct = float(gq["10. change percent"].rstrip("%"))
            volume = int(gq["06. volume"])
            prev_close = float(gq["08. previous close"])
            high = float(gq["03. high"])
            low = float(gq["04. low"])

            name = _TICKER_NAMES.get(symbol, symbol)
            direction = "📈" if change >= 0 else "📉"
            sign = "+" if change >= 0 else ""

            title = f"{direction} {symbol} ({name}) {sign}{change_pct:.2f}% → ${price:.2f}"

            description = (
                f"收盘 ${price:.2f} ({sign}{change:.2f}, {sign}{change_pct:.2f}%) "
                f"| 最高 ${high:.2f} 最低 ${low:.2f} "
                f"| 成交量 {volume:,} | 前收 ${prev_close:.2f}"
            )

            # Flag significant movers
            is_alert = abs(change_pct) >= self.alert_threshold_pct

            return SourceItem(
                title=title,
                url=f"https://finance.yahoo.com/quote/{symbol}",
                source_name="Market Data",
                description=description,
                score=int(change_pct * 100),  # basis points for sorting
                published=datetime.now(),
                extra={
                    "symbol": symbol,
                    "name": name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": volume,
                    "high": high,
                    "low": low,
                    "prev_close": prev_close,
                    "is_alert": is_alert,
                    "is_index": symbol in self.indices,
                },
            )
        except Exception as e:
            logger.warning("[Market Data] Failed to fetch %s: %s", symbol, e)
            return None
