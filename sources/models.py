"""Data models for the pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SourceItem:
    """A single item fetched from any data source."""
    title: str
    url: str
    source_name: str  # e.g. "Hacker News", "arXiv", "Product Hunt"
    description: str = ""
    author: str = ""
    score: Optional[int] = None  # upvotes, stars, etc.
    published: Optional[datetime] = None
    extra: dict = field(default_factory=dict)  # source-specific data


@dataclass
class ProcessedItem:
    """An item after LLM processing."""
    original: SourceItem
    one_line_summary: str = ""
    category: str = ""  # 产品/论文/开源/讨论/新闻
    relevance: str = "medium"  # high/medium/low
    key_insight: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SourceResult:
    """Result from a single source fetch."""
    source_name: str
    items: list[SourceItem] = field(default_factory=list)
    error: Optional[str] = None
    fetch_duration_ms: int = 0


@dataclass
class PipelineResult:
    """Complete pipeline run result."""
    date: str
    sources: list[SourceResult] = field(default_factory=list)
    processed_items: list[ProcessedItem] = field(default_factory=list)
    executive_summary: str = ""
    pdf_path: Optional[str] = None
    email_sent: bool = False
    errors: list[str] = field(default_factory=list)
