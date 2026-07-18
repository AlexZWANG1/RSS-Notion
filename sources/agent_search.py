"""Agent search source — broad AI news discovery via the Kimi CLI's web search.

Replaces Folo (subscription-based) with topic-driven discovery. Runs several
search angles concurrently, then validates every returned URL over HTTP before
letting it into the pipeline: an LLM doing search will occasionally fabricate a
plausible-looking link, and a digest with dead links is worse than a short one.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from sources.base import BaseSource
from sources.models import SourceItem

logger = logging.getLogger(__name__)

# Aggregators / SEO content farms. Search engines rank these highly, but they
# are restatements — the tiering stage treats restatements as C-tier anyway,
# so drop them here rather than spend prompt budget on them.
_DENY_DOMAINS = {
    "abmedia.io", "medium.com", "csdn.net", "cnblogs.com", "zhihu.com",
    "baijiahao.baidu.com", "sohu.com", "163.com", "sina.com.cn", "toutiao.com",
    "makeuseof.com", "analyticsindiamag.com", "marktechpost.com",
}

_DEFAULT_ANGLES = [
    "前沿大模型发布、能力更新与基准测试结果",
    "AI 公司融资、并购、营收与商业化动向",
    "AI Agent、工具链、MCP 与开发者基础设施",
    "值得注意的 AI 研究论文与技术突破",
    "中国 AI 产业动态：模型、算力、政策、落地",
    "AI 安全、监管、政策与治理争议",
]

_PROMPT = """你是 AI 情报分析师。请联网搜索最近 {days} 天（今天是 {today}）关于以下主题的重要新闻：

{angle}

要求：
1. 必须真实联网搜索，**绝对不要编造任何 URL 或标题**。只输出你确实在搜索结果里看到的条目。
2. **优先一手信源**：官方博客、公司公告、论文原文、监管文件。避免二手转述、聚合站、SEO 内容农场。
3. 只要最近 {days} 天内的内容，过期的不要。
4. 最多 {n} 条，按重要性排序。宁缺毋滥——没有重要的就少给几条。

只输出 JSON 数组，不要 markdown 代码块，不要任何解释文字。格式：
[{{"title":"标题","url":"完整URL","source":"来源名称","summary":"两三句话说明这条为什么重要","date":"YYYY-MM-DD"}}]
"""


def _kimi_bin() -> str:
    import shutil

    return (
        os.environ.get("KIMI_BIN")
        or shutil.which("kimi")
        or str(Path.home() / ".kimi-code" / "bin" / "kimi")
    )


def _domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1).lower().removeprefix("www.") if m else "")


class AgentSearchSource(BaseSource):
    """Broad AI-news discovery driven by the Kimi CLI's built-in web search."""

    name = "Agent搜索"
    icon = "🔎"

    def __init__(self, config: dict):
        super().__init__(config)
        self.angles: list[str] = config.get("angles") or _DEFAULT_ANGLES
        self.max_age_days: int = config.get("max_age_days", 7)
        self.per_angle: int = config.get("per_angle", 8)
        self.model: str = config.get(
            "model", "kimi-code/kimi-for-coding-highspeed"
        )
        self.concurrency: int = config.get("concurrency", 3)
        self.timeout: int = config.get("timeout_seconds", 900)

    # -- one search angle -> raw dicts ------------------------------------

    async def _search_angle(self, angle: str) -> list[dict]:
        prompt = _PROMPT.format(
            days=self.max_age_days,
            today=datetime.now().strftime("%Y-%m-%d"),
            angle=angle,
            n=self.per_angle,
        )
        cmd = [
            _kimi_bin(), "-p", prompt,
            "--output-format", "stream-json",
            "-m", self.model,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning("[%s] angle timed out: %s", self.name, angle[:30])
            return []
        except Exception as exc:
            logger.warning("[%s] angle failed (%s): %s", self.name, exc, angle[:30])
            return []

        if proc.returncode != 0:
            logger.warning(
                "[%s] kimi exited %s for angle %s: %s",
                self.name, proc.returncode, angle[:30],
                stderr.decode("utf-8", "replace")[:200],
            )
            return []

        chunks = []
        for line in stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("role") == "assistant":
                chunks.append(evt.get("content", ""))
        text = "".join(chunks).strip()

        # Tolerate a stray code fence or surrounding prose
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            logger.warning("[%s] no JSON array for angle %s", self.name, angle[:30])
            return []
        try:
            rows = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            logger.warning("[%s] bad JSON for angle %s: %s", self.name, angle[:30], exc)
            return []

        return [r for r in rows if isinstance(r, dict) and r.get("url")]

    # -- URL validation ---------------------------------------------------

    async def _jina_verify(self, client: httpx.AsyncClient, url: str) -> bool:
        """Resolve a bot-blocked URL through Jina Reader.

        Jina fetches server-side and prefixes its output with the real page
        title; when the target 404s it emits "Warning: Target URL returned
        error" instead. If Jina itself is unavailable we return True — an
        inconclusive check is not evidence of fabrication.
        """
        try:
            resp = await client.get(f"https://r.jina.ai/{url}", timeout=45)
        except Exception as exc:
            logger.info("[%s] Jina check inconclusive (%s): %s",
                        self.name, type(exc).__name__, url[:80])
            return True
        if resp.status_code >= 400:
            return True
        body = resp.text
        if "Warning: Target URL returned error" in body:
            return False
        return len(body.strip()) > 500

    async def _validate(self, rows: list[dict]) -> list[dict]:
        """Drop fabricated links, keep everything else.

        Only two signals actually indicate fabrication: a 404/410 (the domain
        is real but the model invented the path) and a DNS failure (the model
        invented the domain). A 403/405/429/5xx means bot protection or a
        transient fault — openai.com and anthropic.com both 403 automated
        HEAD/GET, and those are exactly the primary sources we most want to
        keep, so treating any >=400 as dead inverts the source-quality goal.
        """
        limits = httpx.Limits(max_connections=10)

        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, limits=limits,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        ) as client:

            async def check(row: dict) -> Optional[dict]:
                url = row["url"]
                try:
                    resp = await client.head(url)
                    if resp.status_code in (404, 410):
                        resp = await client.get(url)  # some hosts 404 on HEAD
                    if resp.status_code in (404, 410):
                        logger.info("[%s] dropped fabricated path (HTTP %s): %s",
                                    self.name, resp.status_code, url[:80])
                        return None
                    if resp.status_code >= 400:
                        # Cloudflare-style blocks answer 403 before routing, so
                        # a real and a fabricated path on the same host are
                        # indistinguishable by status. Jina renders server-side
                        # and does resolve the difference.
                        if await self._jina_verify(client, url):
                            logger.info("[%s] kept, Jina-verified past HTTP %s: %s",
                                        self.name, resp.status_code, url[:80])
                            return row
                        logger.info("[%s] dropped, Jina says target errors: %s",
                                    self.name, url[:80])
                        return None
                    return row
                except (httpx.ConnectError, httpx.UnsupportedProtocol) as exc:
                    logger.info("[%s] dropped unresolvable domain (%s): %s",
                                self.name, type(exc).__name__, url[:80])
                    return None
                except Exception as exc:
                    # Timeouts and read errors are not evidence of fabrication
                    logger.info("[%s] kept despite %s: %s",
                                self.name, type(exc).__name__, url[:80])
                    return row

            results = await asyncio.gather(*(check(r) for r in rows))

        return [r for r in results if r]

    # -- date verification ------------------------------------------------

    async def _verify_dates(self, rows: list[dict]) -> list[dict]:
        """Confirm each item's claimed date against the page itself.

        This is the check whose absence broke the 2026-07-19 run. A URL that
        resolves proves the page exists, not that it is recent: the model
        surfaced GPT-5.3-Codex (published 2026-02-05), DeepSeek V4
        (2026-04-24) and the Musk v. OpenAI verdict (2026-05-18) as
        last-week news, and every one passed URL validation because the pages
        are real. Search-sourced dates are model-inferred, so an item is
        admitted only when its claimed date actually appears on the page.

        RSS items are unaffected — those dates come from the publisher.
        """
        window = [
            (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range(self.max_age_days + 1)
        ]

        async with httpx.AsyncClient(
            timeout=45, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        ) as client:

            async def confirm(row: dict) -> Optional[dict]:
                claimed = (row.get("date") or "").strip()[:10]
                if claimed not in window:
                    logger.info("[%s] dropped, claimed date %s outside window: %s",
                                self.name, claimed or "(none)", row["url"][:70])
                    return None
                try:
                    resp = await client.get(f"https://r.jina.ai/{row['url']}")
                    body = resp.text if resp.status_code < 400 else ""
                except Exception:
                    body = ""
                if not body:
                    logger.info("[%s] dropped, date unverifiable: %s",
                                self.name, row["url"][:70])
                    return None

                # Accept the claimed day, or any other day inside the window —
                # publishers format dates inconsistently, so a same-window date
                # anywhere on the page is enough corroboration.
                head = body[:6000]
                if any(d in head for d in window):
                    return row
                alt = [d for d in re.findall(r"20\d\d-\d\d-\d\d", head)]
                if alt:
                    logger.info("[%s] dropped, page dates %s not in window: %s",
                                self.name, alt[:3], row["url"][:70])
                else:
                    logger.info("[%s] dropped, no date found on page: %s",
                                self.name, row["url"][:70])
                return None

            results = await asyncio.gather(*(confirm(r) for r in rows))

        return [r for r in results if r]

    # -- main -------------------------------------------------------------

    async def _fetch(self) -> list[SourceItem]:
        sem = asyncio.Semaphore(self.concurrency)

        async def guarded(angle: str) -> list[dict]:
            async with sem:
                return await self._search_angle(angle)

        batches = await asyncio.gather(*(guarded(a) for a in self.angles))

        seen: set[str] = set()
        candidates: list[dict] = []
        for batch in batches:
            for row in batch:
                url = (row.get("url") or "").strip()
                dom = _domain(url)
                if not url.startswith("http") or url in seen:
                    continue
                if dom in _DENY_DOMAINS:
                    logger.info("[%s] dropped aggregator: %s", self.name, dom)
                    continue
                seen.add(url)
                candidates.append(row)

        logger.info("[%s] %d candidates from %d angles, validating URLs...",
                    self.name, len(candidates), len(self.angles))
        rows = await self._validate(candidates)
        logger.info("[%s] %d/%d URLs verified reachable",
                    self.name, len(rows), len(candidates))

        rows = await self._verify_dates(rows)
        logger.info("[%s] %d items survived date verification", self.name, len(rows))

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        items: list[SourceItem] = []
        for row in rows:
            published = None
            raw_date = (row.get("date") or "").strip()
            if raw_date:
                try:
                    published = datetime.strptime(raw_date[:10], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    if published < cutoff:
                        continue
                except ValueError:
                    published = None

            items.append(
                SourceItem(
                    title=(row.get("title") or "").strip(),
                    url=row["url"].strip(),
                    source_name=(row.get("source") or _domain(row["url"])).strip(),
                    description=(row.get("summary") or "").strip(),
                    published=published,
                    extra={"channel": "agent_search"},
                )
            )

        return items
