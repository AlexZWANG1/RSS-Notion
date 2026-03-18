"""Base class for all data sources."""

import abc
import logging
import time
from sources.models import SourceItem, SourceResult

logger = logging.getLogger(__name__)


class BaseSource(abc.ABC):
    """Abstract base class for data sources."""

    name: str = "unknown"
    icon: str = ""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("enabled", True)
        self.max_items = config.get("max_items", 10)

    async def fetch(self) -> SourceResult:
        """Fetch items from this source with error handling."""
        if not self.enabled:
            return SourceResult(source_name=self.name)

        start = time.monotonic()
        try:
            items = await self._fetch()
            duration = int((time.monotonic() - start) * 1000)
            logger.info(f"[{self.name}] Fetched {len(items)} items in {duration}ms")
            return SourceResult(
                source_name=self.name,
                items=items[:self.max_items],
                fetch_duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error(f"[{self.name}] Failed: {e}")
            return SourceResult(
                source_name=self.name,
                error=str(e),
                fetch_duration_ms=duration,
            )

    @abc.abstractmethod
    async def _fetch(self) -> list[SourceItem]:
        """Implement in subclass: fetch raw items from the source."""
        ...
