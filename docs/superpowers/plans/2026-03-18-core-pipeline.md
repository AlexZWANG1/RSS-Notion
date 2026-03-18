# AI Daily Digest — Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python pipeline that aggregates 6 data sources, processes with LLM, generates PDF, and sends email.

**Architecture:** Async Python pipeline with pluggable source modules, OpenAI-powered summarization (via EasyCIL reverse proxy to GPT-5.2), WeasyPrint PDF rendering from Jinja2 templates, and SMTP email delivery. Orchestrated by `main.py` with CLI args.

**Tech Stack:** Python 3.12, asyncio + aiohttp, openai SDK, WeasyPrint, Jinja2, PRAW, arxiv, notion-client, python-dotenv

**Spec:** `docs/superpowers/specs/2026-03-18-ai-daily-digest-design.md`

---

## Chunk 1: Project Scaffolding & Data Models

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `config.json`

- [ ] **Step 1: Create `requirements.txt`**

```
aiohttp>=3.9
arxiv>=2.1
beautifulsoup4>=4.12
jinja2>=3.1
notion-client>=2.2
openai>=1.30
praw>=7.7
python-dotenv>=1.0
weasyprint>=62.0
```

- [ ] **Step 2: Create `.env.example`**

```bash
# OpenAI (via EasyCIL reverse proxy)
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=http://localhost:YOUR_PORT/v1

# Notion (for reading Folo articles)
NOTION_TOKEN=ntn_...

# Reddit OAuth (create "script" app at https://www.reddit.com/prefs/apps)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=ai-daily-digest/1.0

# Product Hunt
PRODUCTHUNT_TOKEN=

# Email SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
EMAIL_RECIPIENTS=recipient@example.com
EMAIL_FROM=AI Daily Digest <your-email@gmail.com>

# Existing (for Phase 1: Folo MCP + Claude CLI)
FOLO_SESSION_TOKEN=
```

- [ ] **Step 3: Update `.gitignore`**

Append to existing `.gitignore`:
```
output/
__pycache__/
*.pyc
.venv/
web/node_modules/
web/.next/
reports.db
*.log
```

- [ ] **Step 4: Extend `config.json`**

Add `pipeline` key to existing config:
```json
{
  "model": "claude-sonnet-4-6",
  "notion": {
    "inbox_database_id": "d1da0a02-bb0f-4dfd-a7d0-8cf918e6f23c",
    "inbox_data_source": "collection://ea643ad3-5c45-435d-b914-e16b46af8ec1",
    "archive_database_id": "fa9724b4-aa43-48ad-8f43-0f902abd760f",
    "archive_data_source": "collection://296ab5f0-a9e7-431a-9097-ba07f24df0aa",
    "config_page_id": "32516831-83e6-8100-b28f-f60937b0d472",
    "research_database_data_source": "collection://2fe16831-83e6-805c-a095-000bab8d1eca"
  },
  "schedule": {
    "times": ["08:00", "18:00"],
    "max_rss_articles": 100,
    "relevance_threshold": 7,
    "max_selected": 15
  },
  "folo": {
    "session_token_env": "FOLO_SESSION_TOKEN"
  },
  "pipeline": {
    "sources": {
      "folo": { "enabled": true },
      "hackernews": { "enabled": true, "max_items": 10 },
      "arxiv": { "enabled": true, "categories": ["cs.AI", "cs.CL", "cs.LG"], "max_items": 20 },
      "reddit": { "enabled": true, "subreddits": ["LocalLLaMA", "MachineLearning"], "max_items": 10 },
      "producthunt": { "enabled": true, "max_items": 10 },
      "github_trending": { "enabled": true, "language": "python", "max_items": 10 }
    },
    "llm": {
      "summary_model": "gpt-5.2",
      "processing_model": "gpt-5.2"
    },
    "pdf": {
      "template": "daily_report",
      "output_dir": "output"
    }
  }
}
```

- [ ] **Step 5: Create directory structure**

```bash
mkdir -p sources generator delivery templates/components api output
touch sources/__init__.py generator/__init__.py delivery/__init__.py api/__init__.py
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore config.json sources/ generator/ delivery/ templates/ api/
git commit -m "chore: scaffold project structure for Python pipeline"
```

### Task 2: Base Source Abstraction & Data Models

**Files:**
- Create: `sources/base.py`
- Create: `sources/models.py`

- [ ] **Step 1: Create `sources/models.py` — shared data types**

```python
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
```

- [ ] **Step 2: Create `sources/base.py` — abstract base**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add sources/models.py sources/base.py
git commit -m "feat: add base source abstraction and data models"
```

---

## Chunk 2: Data Source Modules (Part 1 — No Auth)

### Task 3: Hacker News Source

**Files:**
- Create: `sources/hackernews.py`

- [ ] **Step 1: Implement HN source**

```python
"""Hacker News source via Firebase API."""

import re
import aiohttp
from sources.base import BaseSource
from sources.models import SourceItem

HN_API = "https://hacker-news.firebaseio.com/v0"

AI_KEYWORDS = re.compile(
    r"\b(ai|llm|gpt|claude|openai|anthropic|gemini|machine.?learning|"
    r"deep.?learning|neural|transformer|diffusion|agent|rag|"
    r"fine.?tun|embedding|token|prompt|reasoning|multimodal)\b",
    re.IGNORECASE,
)


class HackerNewsSource(BaseSource):
    name = "Hacker News"
    icon = "🔥"

    async def _fetch(self) -> list[SourceItem]:
        async with aiohttp.ClientSession() as session:
            # Get top story IDs
            async with session.get(f"{HN_API}/topstories.json") as resp:
                story_ids = await resp.json()

            # Fetch top 30 stories in parallel
            items = []
            batch_size = 30
            for sid in story_ids[:batch_size]:
                async with session.get(f"{HN_API}/item/{sid}.json") as resp:
                    story = await resp.json()
                    if not story or story.get("type") != "story":
                        continue
                    title = story.get("title", "")
                    if AI_KEYWORDS.search(title):
                        items.append(SourceItem(
                            title=title,
                            url=story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                            source_name=self.name,
                            description=f"{story.get('score', 0)} points, {story.get('descendants', 0)} comments",
                            author=story.get("by", ""),
                            score=story.get("score", 0),
                        ))
            return sorted(items, key=lambda x: x.score or 0, reverse=True)
```

- [ ] **Step 2: Smoke test — run in isolation**

```bash
python -c "
import asyncio
from sources.hackernews import HackerNewsSource
async def test():
    src = HackerNewsSource({'enabled': True, 'max_items': 5})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  [{item.score}] {item.title}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/hackernews.py
git commit -m "feat: add Hacker News source (Firebase API)"
```

### Task 4: arXiv Source

**Files:**
- Create: `sources/arxiv_source.py`

- [ ] **Step 1: Implement arXiv source**

```python
"""arXiv source via arxiv Python package."""

import asyncio
from datetime import datetime, timedelta, timezone
import arxiv
from sources.base import BaseSource
from sources.models import SourceItem


class ArxivSource(BaseSource):
    name = "arXiv"
    icon = "📄"

    async def _fetch(self) -> list[SourceItem]:
        categories = self.config.get("categories", ["cs.AI", "cs.CL", "cs.LG"])
        query = " OR ".join(f"cat:{cat}" for cat in categories)
        max_results = self.config.get("max_items", 20)

        # arxiv package is synchronous, run in executor
        loop = asyncio.get_event_loop()
        papers = await loop.run_in_executor(
            None, lambda: self._search(query, max_results)
        )
        return papers

    def _search(self, query: str, max_results: int) -> list[SourceItem]:
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        items = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)

        for paper in client.results(search):
            if paper.published.replace(tzinfo=timezone.utc) < cutoff:
                continue
            items.append(SourceItem(
                title=paper.title,
                url=paper.entry_id,
                source_name=self.name,
                description=paper.summary[:500],
                author=", ".join(a.name for a in paper.authors[:3]),
                published=paper.published,
                extra={
                    "categories": [c for c in paper.categories],
                    "pdf_url": paper.pdf_url,
                },
            ))
        return items
```

- [ ] **Step 2: Smoke test**

```bash
python -c "
import asyncio
from sources.arxiv_source import ArxivSource
async def test():
    src = ArxivSource({'enabled': True, 'max_items': 5, 'categories': ['cs.AI', 'cs.CL']})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  {item.title[:80]}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/arxiv_source.py
git commit -m "feat: add arXiv source (arxiv Python package)"
```

### Task 5: GitHub Trending Source

**Files:**
- Create: `sources/github_trending.py`

- [ ] **Step 1: Implement GitHub Trending via Jina Reader**

```python
"""GitHub Trending source via Jina Reader."""

import re
import aiohttp
from sources.base import BaseSource
from sources.models import SourceItem

JINA_PREFIX = "https://r.jina.ai/"
GITHUB_TRENDING_URL = "https://github.com/trending/{language}?since=daily"


class GitHubTrendingSource(BaseSource):
    name = "GitHub Trending"
    icon = "⭐"

    async def _fetch(self) -> list[SourceItem]:
        language = self.config.get("language", "python")
        url = JINA_PREFIX + GITHUB_TRENDING_URL.format(language=language)

        async with aiohttp.ClientSession() as session:
            headers = {"Accept": "text/plain"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    # Fallback: direct scrape
                    return await self._fallback_scrape(session, language)
                text = await resp.text()

        return self._parse_jina_output(text)

    def _parse_jina_output(self, text: str) -> list[SourceItem]:
        """Parse Jina Reader markdown output for trending repos."""
        items = []
        # Jina returns markdown — look for repo patterns
        # Typical pattern: [repo-name](url) followed by description
        repo_pattern = re.compile(
            r'\[([^\]]+/[^\]]+)\]\((https://github\.com/[^\)]+)\)'
        )
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            match = repo_pattern.search(lines[i])
            if match:
                name = match.group(1)
                url = match.group(2)
                # Next non-empty line is likely description
                desc = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    line = lines[j].strip()
                    if line and not line.startswith('[') and not line.startswith('#'):
                        desc = line
                        break
                # Extract stars if present
                stars = 0
                stars_match = re.search(r'([\d,]+)\s*stars?\s*today', text[text.find(name):text.find(name)+500], re.IGNORECASE)
                if stars_match:
                    stars = int(stars_match.group(1).replace(',', ''))

                items.append(SourceItem(
                    title=name,
                    url=url,
                    source_name=self.name,
                    description=desc,
                    score=stars,
                    extra={"language": self.config.get("language", "python")},
                ))
            i += 1
        return items

    async def _fallback_scrape(self, session: aiohttp.ClientSession, language: str) -> list[SourceItem]:
        """Direct scrape as fallback."""
        from bs4 import BeautifulSoup

        url = GITHUB_TRENDING_URL.format(language=language)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        items = []
        for article in soup.select("article.Box-row"):
            h2 = article.select_one("h2 a")
            if not h2:
                continue
            name = h2.get_text(strip=True).replace(" ", "").replace("\n", "")
            href = h2.get("href", "")
            desc_el = article.select_one("p")
            desc = desc_el.get_text(strip=True) if desc_el else ""
            stars_el = article.select_one("span.d-inline-block.float-sm-right")
            stars = 0
            if stars_el:
                stars_text = stars_el.get_text(strip=True).replace(",", "")
                stars = int(re.search(r'\d+', stars_text).group()) if re.search(r'\d+', stars_text) else 0

            items.append(SourceItem(
                title=name,
                url=f"https://github.com{href}",
                source_name=self.name,
                description=desc,
                score=stars,
            ))
        return items
```

- [ ] **Step 2: Smoke test**

```bash
python -c "
import asyncio
from sources.github_trending import GitHubTrendingSource
async def test():
    src = GitHubTrendingSource({'enabled': True, 'max_items': 5, 'language': 'python'})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  [{item.score}] {item.title} — {item.description[:60]}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/github_trending.py
git commit -m "feat: add GitHub Trending source (Jina Reader + BS4 fallback)"
```

---

## Chunk 3: Data Source Modules (Part 2 — Auth Required)

### Task 6: Reddit Source

**Files:**
- Create: `sources/reddit.py`

- [ ] **Step 1: Implement Reddit source via PRAW**

```python
"""Reddit source via PRAW."""

import asyncio
import os
import logging
import praw
from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)


class RedditSource(BaseSource):
    name = "Reddit"
    icon = "💬"

    async def _fetch(self) -> list[SourceItem]:
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        user_agent = os.getenv("REDDIT_USER_AGENT", "ai-daily-digest/1.0")

        if not client_id or not client_secret:
            logger.warning("[Reddit] Missing credentials, trying Jina fallback")
            return await self._fallback_jina()

        subreddits = self.config.get("subreddits", ["LocalLLaMA", "MachineLearning"])

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._fetch_sync(client_id, client_secret, user_agent, subreddits)
        )

    def _fetch_sync(self, client_id, client_secret, user_agent, subreddits) -> list[SourceItem]:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        items = []
        per_sub = self.max_items // len(subreddits) + 1

        for sub_name in subreddits:
            try:
                subreddit = reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=per_sub):
                    if post.stickied:
                        continue
                    items.append(SourceItem(
                        title=post.title,
                        url=f"https://reddit.com{post.permalink}",
                        source_name=f"r/{sub_name}",
                        description=(post.selftext[:500] if post.selftext else ""),
                        author=str(post.author) if post.author else "",
                        score=post.score,
                        extra={"subreddit": sub_name, "num_comments": post.num_comments},
                    ))
            except Exception as e:
                logger.error(f"[Reddit] Failed to fetch r/{sub_name}: {e}")

        return sorted(items, key=lambda x: x.score or 0, reverse=True)

    async def _fallback_jina(self) -> list[SourceItem]:
        """Fallback: scrape via Jina Reader."""
        import aiohttp
        items = []
        subreddits = self.config.get("subreddits", ["LocalLLaMA", "MachineLearning"])

        async with aiohttp.ClientSession() as session:
            for sub in subreddits:
                url = f"https://r.jina.ai/https://www.reddit.com/r/{sub}/hot/"
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        text = await resp.text()
                        # Basic parsing of Jina markdown output
                        for line in text.split('\n'):
                            if line.startswith('# ') or line.startswith('## '):
                                title = line.lstrip('#').strip()
                                if title and len(title) > 10:
                                    items.append(SourceItem(
                                        title=title,
                                        url=f"https://reddit.com/r/{sub}",
                                        source_name=f"r/{sub}",
                                        description="(fetched via fallback)",
                                    ))
                except Exception as e:
                    logger.error(f"[Reddit] Jina fallback failed for r/{sub}: {e}")

        return items
```

- [ ] **Step 2: Smoke test** (requires `.env` with Reddit credentials)

```bash
python -c "
from dotenv import load_dotenv; load_dotenv()
import asyncio
from sources.reddit import RedditSource
async def test():
    src = RedditSource({'enabled': True, 'max_items': 5, 'subreddits': ['LocalLLaMA']})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  [{item.score}] {item.title[:80]}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/reddit.py
git commit -m "feat: add Reddit source (PRAW + Jina fallback)"
```

### Task 7: Product Hunt Source

**Files:**
- Create: `sources/producthunt.py`

- [ ] **Step 1: Implement Product Hunt source**

```python
"""Product Hunt source via GraphQL API with Jina fallback."""

import os
import logging
import aiohttp
from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

PH_API = "https://api.producthunt.com/v2/api/graphql"

POSTS_QUERY = """
query {
  posts(order: RANKING, first: %d) {
    edges {
      node {
        name
        tagline
        description
        url
        votesCount
        website
        topics {
          edges {
            node {
              name
            }
          }
        }
        makers {
          name
        }
      }
    }
  }
}
"""

AI_TOPIC_KEYWORDS = {"artificial intelligence", "ai", "machine learning",
                     "developer tools", "productivity", "saas", "tech"}


class ProductHuntSource(BaseSource):
    name = "Product Hunt"
    icon = "📦"

    async def _fetch(self) -> list[SourceItem]:
        token = os.getenv("PRODUCTHUNT_TOKEN")
        if not token:
            logger.warning("[Product Hunt] No token, using Jina fallback")
            return await self._fallback_jina()

        try:
            return await self._fetch_api(token)
        except Exception as e:
            logger.warning(f"[Product Hunt] API failed ({e}), trying Jina fallback")
            return await self._fallback_jina()

    async def _fetch_api(self, token: str) -> list[SourceItem]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        query = POSTS_QUERY % (self.max_items * 2)

        async with aiohttp.ClientSession() as session:
            async with session.post(PH_API, json={"query": query}, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()

        items = []
        for edge in data.get("data", {}).get("posts", {}).get("edges", []):
            node = edge["node"]
            topics = {t["node"]["name"].lower() for t in node.get("topics", {}).get("edges", [])}
            # Filter for AI-related
            if not topics & AI_TOPIC_KEYWORDS:
                # Check name/tagline for AI keywords
                text = f"{node['name']} {node['tagline']}".lower()
                if not any(kw in text for kw in ["ai", "gpt", "llm", "machine learning", "agent"]):
                    continue

            items.append(SourceItem(
                title=node["name"],
                url=node.get("website") or node["url"],
                source_name=self.name,
                description=node.get("tagline", ""),
                score=node.get("votesCount", 0),
                extra={
                    "topics": list(topics),
                    "full_description": node.get("description", ""),
                },
            ))
        return items

    async def _fallback_jina(self) -> list[SourceItem]:
        """Scrape Product Hunt via Jina Reader."""
        url = "https://r.jina.ai/https://www.producthunt.com/"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()

        # Parse markdown for product entries
        items = []
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('## ') or line.startswith('### '):
                title = line.lstrip('#').strip()
                desc = ""
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].strip():
                        desc = lines[j].strip()
                        break
                if title and len(title) > 3:
                    items.append(SourceItem(
                        title=title,
                        url="https://www.producthunt.com",
                        source_name=self.name,
                        description=desc,
                    ))
        return items
```

- [ ] **Step 2: Smoke test**

```bash
python -c "
from dotenv import load_dotenv; load_dotenv()
import asyncio
from sources.producthunt import ProductHuntSource
async def test():
    src = ProductHuntSource({'enabled': True, 'max_items': 5})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  [{item.score}] {item.title} — {item.description[:60]}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/producthunt.py
git commit -m "feat: add Product Hunt source (GraphQL API + Jina fallback)"
```

### Task 8: Folo/Notion Source

**Files:**
- Create: `sources/folo.py`

- [ ] **Step 1: Implement Folo source (reads from Notion)**

```python
"""Folo RSS source — reads today's articles from Notion inbox."""

import os
import logging
from datetime import date
import asyncio
from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)


class FoloSource(BaseSource):
    name = "RSS精选 (Folo)"
    icon = "📰"

    async def _fetch(self) -> list[SourceItem]:
        token = os.getenv("NOTION_TOKEN")
        if not token:
            logger.warning("[Folo] No NOTION_TOKEN, skipping")
            return []

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._query_notion(token))

    def _query_notion(self, token: str) -> list[SourceItem]:
        from notion_client import Client

        notion = Client(auth=token)
        today = date.today().isoformat()

        # Query inbox database for today's Folo articles
        database_id = self.config.get("database_id", "")
        if not database_id:
            # Try to read from parent config
            import json
            try:
                with open("config.json") as f:
                    cfg = json.load(f)
                database_id = cfg["notion"]["inbox_database_id"]
            except Exception:
                logger.error("[Folo] No database_id configured")
                return []

        response = notion.databases.query(
            database_id=database_id,
            filter={
                "and": [
                    {
                        "property": "来源",
                        "select": {"equals": "RSS精选"},
                    },
                    {
                        "property": "收录时间",
                        "date": {"equals": today},
                    },
                ]
            },
        )

        items = []
        for page in response.get("results", []):
            props = page.get("properties", {})

            title = ""
            title_prop = props.get("名称", {}) or props.get("Name", {})
            if title_prop.get("title"):
                title = "".join(t.get("plain_text", "") for t in title_prop["title"])
            title = title.replace("[AI精选] ", "").replace("[视频摘要] ", "")

            url = ""
            url_prop = props.get("原文链接", {})
            if url_prop and url_prop.get("url"):
                url = url_prop["url"]

            topic = ""
            topic_prop = props.get("话题", {})
            if topic_prop and topic_prop.get("select"):
                topic = topic_prop["select"].get("name", "")

            importance = ""
            imp_prop = props.get("重要性", {})
            if imp_prop and imp_prop.get("select"):
                importance = imp_prop["select"].get("name", "")

            media = ""
            media_prop = props.get("媒体来源", {})
            if media_prop and media_prop.get("rich_text"):
                media = "".join(t.get("plain_text", "") for t in media_prop["rich_text"])

            items.append(SourceItem(
                title=title,
                url=url,
                source_name=media or self.name,
                description=f"话题: {topic}" if topic else "",
                extra={"importance": importance, "topic": topic},
            ))

        logger.info(f"[Folo] Found {len(items)} articles from Notion for {today}")
        return items
```

- [ ] **Step 2: Smoke test** (requires NOTION_TOKEN in .env)

```bash
python -c "
from dotenv import load_dotenv; load_dotenv()
import asyncio
from sources.folo import FoloSource
async def test():
    src = FoloSource({'enabled': True, 'max_items': 15})
    result = await src.fetch()
    print(f'Items: {len(result.items)}, Error: {result.error}')
    for item in result.items:
        print(f'  {item.title[:60]} — {item.source_name}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add sources/folo.py
git commit -m "feat: add Folo source (reads today's articles from Notion API)"
```

---

## Chunk 4: LLM Processing

### Task 9: OpenAI Summarizer

**Files:**
- Create: `generator/summarizer.py`

- [ ] **Step 1: Implement LLM processor**

```python
"""LLM-powered content processing and summarization via OpenAI API."""

import os
import json
import logging
import asyncio
from openai import AsyncOpenAI
from sources.models import SourceItem, ProcessedItem

logger = logging.getLogger(__name__)


def _get_client() -> AsyncOpenAI:
    """Create OpenAI client (supports EasyCIL reverse proxy)."""
    base_url = os.getenv("OPENAI_BASE_URL")
    return AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=base_url if base_url else None,
    )


async def process_items_batch(
    items: list[SourceItem],
    model: str = "gpt-5.2",
    max_retries: int = 2,
) -> list[ProcessedItem]:
    """Process a batch of items with LLM for structured extraction."""
    if not items:
        return []

    client = _get_client()

    # Batch items into groups of 10 for efficient API usage
    batch_size = 10
    all_processed = []

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        items_text = "\n---\n".join(
            f"[{j+1}] Title: {item.title}\n"
            f"Source: {item.source_name}\n"
            f"Description: {item.description[:300]}"
            for j, item in enumerate(batch)
        )

        prompt = f"""你是一个AI领域的信息分析助手。请对以下{len(batch)}条信息逐一进行结构化处理。

{items_text}

请以JSON数组格式返回，每个元素包含：
- "index": 序号（从1开始）
- "one_line_summary": 一句话中文摘要（20-40字）
- "category": 分类（产品/论文/开源/讨论/新闻 之一）
- "relevance": 相关性（high/medium/low）
- "key_insight": 核心亮点（一句话）
- "tags": 标签数组（2-4个英文标签）

只返回JSON数组，不要其他内容。"""

        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        response_format={"type": "json_object"},
                    ),
                    timeout=60,
                )
                content = response.choices[0].message.content
                # Parse JSON - handle both array and wrapped formats
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    parsed = parsed.get("items", parsed.get("results", [parsed]))
                if not isinstance(parsed, list):
                    parsed = [parsed]

                for j, item in enumerate(batch):
                    entry = parsed[j] if j < len(parsed) else {}
                    all_processed.append(ProcessedItem(
                        original=item,
                        one_line_summary=entry.get("one_line_summary", item.title),
                        category=entry.get("category", "新闻"),
                        relevance=entry.get("relevance", "medium"),
                        key_insight=entry.get("key_insight", ""),
                        tags=entry.get("tags", []),
                    ))
                break  # Success

            except asyncio.TimeoutError:
                logger.warning(f"[LLM] Timeout on batch (attempt {attempt + 1})")
                if attempt == max_retries:
                    # Fallback: create unprocessed items
                    for item in batch:
                        all_processed.append(ProcessedItem(original=item))
            except Exception as e:
                logger.warning(f"[LLM] Error on batch (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1 * (2 ** attempt))
                else:
                    for item in batch:
                        all_processed.append(ProcessedItem(original=item))

    await client.close()
    return all_processed


async def generate_executive_summary(
    processed_items: list[ProcessedItem],
    model: str = "gpt-5.2",
) -> str:
    """Generate executive summary from all processed items."""
    if not processed_items:
        return "今日暂无AI领域重要动态。"

    client = _get_client()

    items_text = "\n".join(
        f"- [{item.category}] {item.one_line_summary} (相关性: {item.relevance})"
        for item in processed_items
        if item.relevance in ("high", "medium")
    )

    prompt = f"""你是一位资深AI行业分析师。基于今日收集到的以下信息，撰写一份200-400字的中文"每日总结"（Executive Summary）。

{items_text}

要求：
1. 提炼今日最值得关注的3个趋势或亮点
2. 指出跨信息源的关联和主题
3. 语言简洁专业，有洞察力
4. 不要使用"总结"作为开头，直接进入内容

请直接输出总结文本，不需要标题或格式标记。"""

    for attempt in range(3):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                ),
                timeout=60,
            )
            await client.close()
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[LLM] Summary error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                await asyncio.sleep(1 * (2 ** attempt))

    await client.close()
    return "今日AI领域动态丰富，涵盖多个技术方向和应用场景，详见下方各板块内容。"
```

- [ ] **Step 2: Smoke test**

```bash
python -c "
from dotenv import load_dotenv; load_dotenv()
import asyncio
from sources.models import SourceItem
from generator.summarizer import process_items_batch, generate_executive_summary

async def test():
    items = [
        SourceItem(title='Claude 4 released', url='https://example.com', source_name='HN', description='Anthropic releases Claude 4 with improved reasoning'),
        SourceItem(title='PyTorch 3.0', url='https://example.com', source_name='GitHub', description='Major update to PyTorch framework'),
    ]
    processed = await process_items_batch(items)
    print('Processed:')
    for p in processed:
        print(f'  {p.one_line_summary} [{p.category}] tags={p.tags}')
    summary = await generate_executive_summary(processed)
    print(f'\nSummary:\n{summary}')

asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add generator/summarizer.py
git commit -m "feat: add LLM summarizer (OpenAI batch processing + executive summary)"
```

---

## Chunk 5: PDF Generation & Email

### Task 10: PDF Template & Builder

**Files:**
- Create: `templates/daily_report.html`
- Create: `templates/styles.css`
- Create: `generator/pdf_builder.py`

- [ ] **Step 1: Create `templates/styles.css`**

```css
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');

:root {
    --primary: #1a1a2e;
    --secondary: #16213e;
    --accent: #0f3460;
    --highlight: #e94560;
    --bg: #ffffff;
    --bg-alt: #f8f9fa;
    --text: #333333;
    --text-light: #666666;
    --border: #e0e0e0;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 10pt;
    line-height: 1.6;
    color: var(--text);
    background: var(--bg);
}

/* Header */
.header {
    background: linear-gradient(135deg, var(--primary), var(--accent));
    color: white;
    padding: 30px 40px;
    margin-bottom: 20px;
}

.header h1 {
    font-size: 22pt;
    font-weight: 700;
    margin-bottom: 5px;
}

.header .date {
    font-size: 11pt;
    opacity: 0.8;
}

.header .stats {
    margin-top: 10px;
    font-size: 9pt;
    opacity: 0.7;
}

/* Executive Summary */
.summary {
    background: var(--bg-alt);
    border-left: 4px solid var(--highlight);
    padding: 20px 25px;
    margin: 0 30px 25px 30px;
    font-size: 10.5pt;
    line-height: 1.8;
}

.summary h2 {
    font-size: 13pt;
    color: var(--primary);
    margin-bottom: 10px;
}

/* Section */
.section {
    margin: 0 30px 25px 30px;
    page-break-inside: avoid;
}

.section-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 0;
    border-bottom: 2px solid var(--primary);
    margin-bottom: 15px;
}

.section-header .icon {
    font-size: 16pt;
}

.section-header h2 {
    font-size: 13pt;
    color: var(--primary);
    font-weight: 700;
}

.section-header .count {
    font-size: 9pt;
    color: var(--text-light);
    margin-left: auto;
}

/* Item table */
.items-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 9pt;
}

.items-table th {
    background: var(--primary);
    color: white;
    padding: 8px 10px;
    text-align: left;
    font-weight: 500;
}

.items-table td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
}

.items-table tr:nth-child(even) {
    background: var(--bg-alt);
}

.items-table .title {
    font-weight: 500;
    color: var(--accent);
}

.items-table .summary-text {
    color: var(--text-light);
    font-size: 8.5pt;
}

.items-table .tags {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
}

.items-table .tag {
    background: #e8edf3;
    color: var(--accent);
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 7.5pt;
}

.items-table .score {
    font-weight: 700;
    color: var(--highlight);
}

/* Unavailable source */
.source-unavailable {
    color: var(--text-light);
    font-style: italic;
    padding: 15px;
    text-align: center;
    background: var(--bg-alt);
    border-radius: 4px;
}

/* Footer */
.footer {
    margin-top: 30px;
    padding: 15px 40px;
    border-top: 1px solid var(--border);
    font-size: 8pt;
    color: var(--text-light);
    text-align: center;
}

/* Page break hints */
@media print {
    .section { page-break-inside: avoid; }
    .header { page-break-after: avoid; }
}
```

- [ ] **Step 2: Create `templates/daily_report.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <!-- Header -->
    <div class="header">
        <h1>AI 认知日报</h1>
        <div class="date">{{ date }}</div>
        <div class="stats">
            共聚合 {{ total_items }} 条信息，来自 {{ active_sources }} 个数据源
        </div>
    </div>

    <!-- Executive Summary -->
    <div class="summary">
        <h2>📋 今日总结</h2>
        <p>{{ executive_summary }}</p>
    </div>

    <!-- Source Sections -->
    {% for section in sections %}
    <div class="section">
        <div class="section-header">
            <span class="icon">{{ section.icon }}</span>
            <h2>{{ section.title }}</h2>
            <span class="count">{{ section.items | length }} 条</span>
        </div>

        {% if section.error %}
        <div class="source-unavailable">
            ⚠️ 数据源暂不可用：{{ section.error }}
        </div>
        {% elif section.items %}
        <table class="items-table">
            <thead>
                <tr>
                    <th style="width: 5%">#</th>
                    <th style="width: 35%">标题</th>
                    <th style="width: 30%">摘要</th>
                    <th style="width: 15%">标签</th>
                    <th style="width: 15%">热度</th>
                </tr>
            </thead>
            <tbody>
                {% for item in section.items %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td>
                        <div class="title">{{ item.title }}</div>
                        <div class="summary-text">{{ item.source_name }}</div>
                    </td>
                    <td class="summary-text">{{ item.one_line_summary }}</td>
                    <td>
                        <div class="tags">
                            {% for tag in item.tags %}
                            <span class="tag">{{ tag }}</span>
                            {% endfor %}
                        </div>
                    </td>
                    <td class="score">
                        {% if item.score %}{{ item.score }}{% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="source-unavailable">
            今日暂无相关内容
        </div>
        {% endif %}
    </div>
    {% endfor %}

    <!-- Footer -->
    <div class="footer">
        Generated by AI Daily Digest | {{ date }} | Powered by GPT + WeasyPrint
    </div>
</body>
</html>
```

- [ ] **Step 3: Create `generator/pdf_builder.py`**

```python
"""PDF report generation via WeasyPrint + Jinja2."""

import logging
import os
from pathlib import Path
from datetime import date
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from sources.models import ProcessedItem, SourceResult

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Section display order and config
SECTION_CONFIG = [
    {"source_key": "Product Hunt", "title": "新产品", "icon": "📦"},
    {"source_key": "Hacker News", "title": "热门讨论", "icon": "🔥"},
    {"source_key": "RSS精选 (Folo)", "title": "RSS 精选", "icon": "📰"},
    {"source_key": "arXiv", "title": "学术论文", "icon": "📄"},
    {"source_key": "Reddit", "title": "社区动态", "icon": "💬"},
    {"source_key": "GitHub Trending", "title": "开源项目", "icon": "⭐"},
]


def build_pdf(
    source_results: list[SourceResult],
    processed_items: list[ProcessedItem],
    executive_summary: str,
    output_dir: str = "output",
    report_date: str | None = None,
) -> str:
    """Generate PDF report. Returns the output file path."""
    report_date = report_date or date.today().isoformat()
    out_dir = Path(output_dir) / report_date
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "report.pdf"

    # Build section data
    # Map processed items by source
    items_by_source: dict[str, list] = {}
    for pi in processed_items:
        src = pi.original.source_name
        # Normalize Reddit sub-sources
        if src.startswith("r/"):
            src = "Reddit"
        items_by_source.setdefault(src, []).append(pi)

    # Map errors by source
    errors_by_source = {sr.source_name: sr.error for sr in source_results if sr.error}

    sections = []
    for cfg in SECTION_CONFIG:
        key = cfg["source_key"]
        items = items_by_source.get(key, [])
        error = errors_by_source.get(key)

        section_items = []
        for pi in items:
            section_items.append({
                "title": pi.original.title,
                "source_name": pi.original.source_name,
                "one_line_summary": pi.one_line_summary or pi.original.description[:100],
                "tags": pi.tags,
                "score": pi.original.score,
                "url": pi.original.url,
            })

        sections.append({
            "title": cfg["title"],
            "icon": cfg["icon"],
            "items": section_items,
            "error": error,
        })

    # Count totals
    total_items = sum(len(s["items"]) for s in sections)
    active_sources = sum(1 for s in sections if s["items"] or not s.get("error"))

    # Render HTML
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("daily_report.html")
    html_content = template.render(
        date=report_date,
        executive_summary=executive_summary,
        sections=sections,
        total_items=total_items,
        active_sources=active_sources,
    )

    # Generate PDF
    base_url = str(TEMPLATES_DIR)
    HTML(string=html_content, base_url=base_url).write_pdf(str(pdf_path))

    logger.info(f"[PDF] Generated: {pdf_path} ({pdf_path.stat().st_size / 1024:.1f} KB)")
    return str(pdf_path)
```

- [ ] **Step 4: Test PDF generation with mock data**

```bash
python -c "
from sources.models import SourceItem, ProcessedItem, SourceResult
from generator.pdf_builder import build_pdf

# Mock data
items = [
    ProcessedItem(
        original=SourceItem(title='Test Product', url='https://example.com', source_name='Product Hunt', description='A cool AI tool', score=100),
        one_line_summary='一个很酷的AI工具', category='产品', relevance='high', key_insight='创新的交互方式', tags=['ai', 'tool'],
    ),
    ProcessedItem(
        original=SourceItem(title='LLM Paper', url='https://arxiv.org/123', source_name='arXiv', description='New architecture'),
        one_line_summary='提出了新的LLM架构', category='论文', relevance='high', tags=['llm', 'architecture'],
    ),
]
sources = [SourceResult(source_name='Product Hunt', items=[]), SourceResult(source_name='arXiv', items=[])]
path = build_pdf(sources, items, '今日AI领域最值得关注的是...', output_dir='output')
print(f'PDF generated: {path}')
"
```

- [ ] **Step 5: Commit**

```bash
git add templates/ generator/pdf_builder.py
git commit -m "feat: add PDF generation (WeasyPrint + Jinja2 templates)"
```

### Task 11: Email Delivery

**Files:**
- Create: `delivery/emailer.py`

- [ ] **Step 1: Implement emailer**

```python
"""Email delivery via SMTP."""

import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

logger = logging.getLogger(__name__)


def send_report_email(
    pdf_path: str,
    executive_summary: str,
    report_date: str,
) -> bool:
    """Send the daily digest email with PDF attachment. Returns True on success."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    recipients = os.getenv("EMAIL_RECIPIENTS", "")
    from_addr = os.getenv("EMAIL_FROM", user)

    if not all([host, user, password, recipients]):
        logger.warning("[Email] Missing SMTP configuration, skipping email")
        return False

    recipient_list = [r.strip() for r in recipients.split(",") if r.strip()]
    if not recipient_list:
        logger.warning("[Email] No recipients configured")
        return False

    # Build email
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipient_list)
    msg["Subject"] = f"[AI日报] {report_date} 每日认知日报"

    # Body
    body = f"""AI 认知日报 — {report_date}

{executive_summary}

---
完整日报请查看附件PDF。
本邮件由 AI Daily Digest 自动生成。"""

    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attach PDF
    pdf_file = Path(pdf_path)
    if pdf_file.exists():
        with open(pdf_file, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="pdf")
            attachment.add_header(
                "Content-Disposition", "attachment",
                filename=f"ai-daily-digest-{report_date}.pdf",
            )
            msg.attach(attachment)
    else:
        logger.error(f"[Email] PDF not found: {pdf_path}")
        return False

    # Send
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        logger.info(f"[Email] Sent to {len(recipient_list)} recipients")
        return True
    except Exception as e:
        logger.error(f"[Email] Failed: {e}")
        return False
```

- [ ] **Step 2: Commit**

```bash
git add delivery/emailer.py
git commit -m "feat: add email delivery (SMTP with PDF attachment)"
```

---

## Chunk 6: Pipeline Orchestrator & Entry Points

### Task 12: Main Pipeline

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement `main.py`**

```python
"""AI Daily Digest — main pipeline entry point."""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sources.hackernews import HackerNewsSource
from sources.arxiv_source import ArxivSource
from sources.reddit import RedditSource
from sources.producthunt import ProductHuntSource
from sources.github_trending import GitHubTrendingSource
from sources.folo import FoloSource
from sources.models import SourceResult, PipelineResult
from generator.summarizer import process_items_batch, generate_executive_summary
from generator.pdf_builder import build_pdf
from delivery.emailer import send_report_email

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


SOURCE_CLASSES = {
    "hackernews": HackerNewsSource,
    "arxiv": ArxivSource,
    "reddit": RedditSource,
    "producthunt": ProductHuntSource,
    "github_trending": GitHubTrendingSource,
    "folo": FoloSource,
}


async def run_pipeline(
    config: dict,
    skip_email: bool = False,
    skip_notion: bool = False,
    only_sources: list[str] | None = None,
) -> PipelineResult:
    """Run the full pipeline."""
    today = date.today().isoformat()
    pipeline_cfg = config.get("pipeline", {})
    sources_cfg = pipeline_cfg.get("sources", {})
    llm_cfg = pipeline_cfg.get("llm", {})
    pdf_cfg = pipeline_cfg.get("pdf", {})

    result = PipelineResult(date=today)

    # --- Phase: Fetch sources ---
    logger.info("=" * 50)
    logger.info(f"Starting AI Daily Digest for {today}")
    logger.info("=" * 50)

    logger.info("Phase 1: Fetching data sources...")

    source_instances = []
    for name, cls in SOURCE_CLASSES.items():
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", True):
            continue
        if only_sources and name not in only_sources:
            continue
        if name == "folo" and skip_notion:
            continue
        source_instances.append(cls(cfg))

    # Fetch all sources concurrently
    fetch_tasks = [src.fetch() for src in source_instances]
    source_results: list[SourceResult] = await asyncio.gather(*fetch_tasks)
    result.sources = source_results

    # Collect all items
    all_items = []
    for sr in source_results:
        all_items.extend(sr.items)
        if sr.error:
            result.errors.append(f"{sr.source_name}: {sr.error}")

    logger.info(f"Fetched {len(all_items)} items from {len(source_results)} sources")

    if not all_items:
        logger.warning("No items fetched, generating minimal report")

    # --- Phase: LLM processing ---
    logger.info("Phase 2: LLM processing...")
    model = llm_cfg.get("processing_model", "gpt-5.2")
    summary_model = llm_cfg.get("summary_model", "gpt-5.2")

    processed = await process_items_batch(all_items, model=model)
    result.processed_items = processed
    logger.info(f"Processed {len(processed)} items")

    # Executive summary
    logger.info("Generating executive summary...")
    summary = await generate_executive_summary(processed, model=summary_model)
    result.executive_summary = summary

    # --- Phase: PDF generation ---
    logger.info("Phase 3: Generating PDF...")
    output_dir = pdf_cfg.get("output_dir", "output")
    pdf_path = build_pdf(source_results, processed, summary, output_dir, today)
    result.pdf_path = pdf_path

    # Save data.json alongside PDF
    data_dir = Path(output_dir) / today
    data_path = data_dir / "data.json"
    data_json = {
        "date": today,
        "executive_summary": summary,
        "sources": [
            {
                "name": sr.source_name,
                "item_count": len(sr.items),
                "error": sr.error,
                "duration_ms": sr.fetch_duration_ms,
            }
            for sr in source_results
        ],
        "items": [
            {
                "title": pi.original.title,
                "url": pi.original.url,
                "source": pi.original.source_name,
                "summary": pi.one_line_summary,
                "category": pi.category,
                "relevance": pi.relevance,
                "tags": pi.tags,
                "score": pi.original.score,
            }
            for pi in processed
        ],
    }
    data_path.write_text(json.dumps(data_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Phase: Email ---
    if not skip_email:
        logger.info("Phase 4: Sending email...")
        result.email_sent = send_report_email(pdf_path, summary, today)
    else:
        logger.info("Phase 4: Email skipped (--skip-email)")

    # --- Done ---
    logger.info("=" * 50)
    logger.info(f"Pipeline complete! PDF: {pdf_path}")
    if result.errors:
        logger.warning(f"Errors: {result.errors}")
    logger.info("=" * 50)

    return result


def main():
    parser = argparse.ArgumentParser(description="AI Daily Digest Pipeline")
    parser.add_argument("--skip-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--skip-notion", action="store_true", help="Skip Folo/Notion source")
    parser.add_argument("--sources", type=str, help="Comma-separated source names to run (e.g. hackernews,arxiv)")
    args = parser.parse_args()

    only_sources = args.sources.split(",") if args.sources else None

    result = asyncio.run(run_pipeline(
        config=load_config(),
        skip_email=args.skip_email,
        skip_notion=args.skip_notion,
        only_sources=only_sources,
    ))

    sys.exit(0 if not result.errors else 0)  # Don't fail on source errors


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test with a single source**

```bash
python main.py --skip-email --skip-notion --sources hackernews
```

Expected: Fetches HN, processes via LLM, generates PDF in `output/{date}/report.pdf`

- [ ] **Step 3: Test full pipeline (except email)**

```bash
python main.py --skip-email
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add main pipeline orchestrator with CLI args"
```

### Task 13: Shell Entry Points

**Files:**
- Create: `run.sh`
- Create: `run.bat`

- [ ] **Step 1: Create `run.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[$(date)] Starting AI Daily Digest..."

# Phase 1: Folo + Notion (optional, may fail)
if command -v node &> /dev/null && [ -f daily-digest.mjs ]; then
    echo "[$(date)] Phase 1: Running Folo + Notion pipeline..."
    node daily-digest.mjs || echo "[$(date)] WARNING: Phase 1 failed, continuing..."
else
    echo "[$(date)] Phase 1: Skipped (Node.js or daily-digest.mjs not found)"
fi

# Phase 2: Python pipeline
echo "[$(date)] Phase 2: Running Python pipeline..."
python main.py "$@"

echo "[$(date)] Done!"
```

- [ ] **Step 2: Create `run.bat`**

```batch
@echo off
cd /d "%~dp0"

echo [%date% %time%] Starting AI Daily Digest...

:: Phase 1: Folo + Notion (optional)
where node >nul 2>&1
if %errorlevel% equ 0 (
    if exist daily-digest.mjs (
        echo [%date% %time%] Phase 1: Running Folo + Notion pipeline...
        node daily-digest.mjs
        if %errorlevel% neq 0 echo [%date% %time%] WARNING: Phase 1 failed, continuing...
    )
) else (
    echo [%date% %time%] Phase 1: Skipped
)

:: Phase 2: Python pipeline
echo [%date% %time%] Phase 2: Running Python pipeline...
python main.py %*

echo [%date% %time%] Done!
```

- [ ] **Step 3: Make run.sh executable and commit**

```bash
chmod +x run.sh
git add run.sh run.bat
git commit -m "feat: add shell entry points (run.sh + run.bat)"
```

### Task 14: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/daily-digest.yml`

- [ ] **Step 1: Create workflow file**

```yaml
name: Daily AI Digest
# Phase 2 only (Python pipeline). Folo/Notion requires local Claude CLI.

on:
  schedule:
    - cron: '0 0 * * *'   # 08:00 UTC+8
    - cron: '0 10 * * *'  # 18:00 UTC+8
  workflow_dispatch: {}

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Chinese fonts
        run: sudo apt-get install -y fonts-noto-cjk

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run pipeline
        run: python main.py --skip-notion
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENAI_BASE_URL: ${{ secrets.OPENAI_BASE_URL }}
          REDDIT_CLIENT_ID: ${{ secrets.REDDIT_CLIENT_ID }}
          REDDIT_CLIENT_SECRET: ${{ secrets.REDDIT_CLIENT_SECRET }}
          PRODUCTHUNT_TOKEN: ${{ secrets.PRODUCTHUNT_TOKEN }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          EMAIL_RECIPIENTS: ${{ secrets.EMAIL_RECIPIENTS }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}

      - name: Upload report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: daily-report-${{ github.run_id }}
          path: output/
```

- [ ] **Step 2: Create `.github/workflows` directory and commit**

```bash
mkdir -p .github/workflows
git add .github/workflows/daily-digest.yml
git commit -m "feat: add GitHub Actions workflow for scheduled execution"
```

---

## Chunk 7: Documentation & Final Integration Test

### Task 15: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README.md**

See spec for full content. Key sections:
- Project overview
- Quick start (clone, pip install, configure .env, run)
- Configuration guide (each API key with instructions)
- Usage (CLI args, scheduling options)
- Architecture diagram
- Contributing

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add comprehensive README with setup guide"
```

### Task 16: End-to-End Test

- [ ] **Step 1: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 2: Configure `.env`** from `.env.example`

- [ ] **Step 3: Run full pipeline**

```bash
python main.py --skip-email
```

Verify:
- `output/{date}/report.pdf` exists and looks correct
- `output/{date}/data.json` has all source data
- Console shows all 6 sources fetched (or gracefully skipped)

- [ ] **Step 4: Test email delivery**

```bash
python main.py --sources hackernews
```

Verify email arrives with PDF attachment.

- [ ] **Step 5: Run via shell script**

```bash
./run.sh --skip-email
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: complete core pipeline — multi-source aggregation, LLM processing, PDF generation, email delivery"
```
