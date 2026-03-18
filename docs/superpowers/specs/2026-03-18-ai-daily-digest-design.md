# AI Daily Digest - Design Specification

## Overview

Transform the existing RSS-Notion project into a comprehensive AI daily digest system that aggregates content from 6 data sources, generates professional PDF reports via LLM, sends them by email, and provides a web UI for management — while preserving the existing Notion workflow.

## Architecture

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Orchestrator (run.sh / main.py)        │
├────────────────────────┬─────────────────────────────────┤
│  Phase 1: Folo + Notion│  Phase 2: Python Pipeline       │
│  (existing Node.js)    │  (new)                          │
│                        │                                 │
│  daily-digest.mjs      │  1. Multi-source aggregation    │
│  → Claude CLI + MCP    │     (incl. Notion query for     │
│  → Notion write        │      Folo articles)             │
│                        │  2. LLM structured processing   │
│                        │  3. WeasyPrint PDF generation   │
│                        │  4. SMTP email delivery         │
├────────────────────────┴─────────────────────────────────┤
│  Phase 3: Web Service (optional, long-running)           │
│  FastAPI backend + Next.js frontend                      │
└──────────────────────────────────────────────────────────┘
```

### Execution Flow

```
run.sh / run.bat (single command entry point)
│
├─ Step 1: node daily-digest.mjs (optional, skipped on CI)
│   ├─ Folo MCP → fetch RSS articles
│   ├─ Claude CLI → score & filter
│   └─ Notion MCP → write to inbox
│
├─ Step 2: python main.py
│   ├─ Query Notion API for today's Folo articles (if available)
│   ├─ Fetch from 5 other sources (parallel, via asyncio + aiohttp)
│   ├─ OpenAI API → structured processing per item
│   ├─ OpenAI API → executive summary
│   ├─ WeasyPrint → render PDF
│   └─ smtplib → send email with PDF attachment
│
└─ (Optional) Start web service
    ├─ FastAPI backend (api/server.py)
    └─ Next.js frontend (web/)
```

## Data Sources

### Source 1: Folo RSS (existing)

- **Method**: Python queries Notion API directly for today's Folo-written articles
- **Data**: User's personalized RSS subscriptions (already written to Notion by `daily-digest.mjs`)
- **Integration**: The Python `folo.py` source module uses the Notion API (`notion-client` Python SDK) to query the inbox database for items with today's date and source = "RSS精选" or "视频摘要". This avoids fragile LLM output parsing.
- **Auth**: Notion integration token (added to `.env`)
- **Fallback**: If Notion query returns 0 results (e.g., Phase 1 didn't run or running on CI), Folo source is gracefully skipped with a note in the PDF report.
- **Data extracted from Notion**:
  ```json
  {
    "title": "...",
    "url": "原文链接 property",
    "source": "媒体来源 property",
    "topic": "话题 property",
    "importance": "重要性 property",
    "summary": "page content (truncated to 500 chars)"
  }
  ```

### Source 2: Hacker News

- **Method**: Official Firebase API (`https://hacker-news.firebaseio.com/v0/`)
- **Auth**: None required
- **Rate limit**: None
- **Endpoints**:
  - `topstories.json` → get top story IDs
  - `item/{id}.json` → get story details
- **Filter**: AI-related keywords in title (ai, llm, gpt, machine learning, neural, transformer, agent, etc.)
- **Fetch**: Top 30 stories, filter to AI-related, take top 10

### Source 3: arXiv

- **Method**: `arxiv` Python package
- **Auth**: None required
- **Rate limit**: 3-second delay between calls recommended
- **Query**: `cat:cs.AI OR cat:cs.CL OR cat:cs.LG`, sorted by `submittedDate`, last 24 hours
- **Fetch**: Up to 20 papers, extract title, authors, abstract, categories, URL

### Source 4: Reddit

- **Method**: PRAW (Python Reddit API Wrapper) — handles OAuth token refresh and rate limiting automatically
- **Auth**: Reddit "script" type application (free tier, non-commercial)
- **Rate limit**: 100 requests/minute (OAuth), 10 requests/minute (unauthenticated)
- **Subreddits**: `r/LocalLLaMA`, `r/MachineLearning`
- **Fetch**: Top 10 hot posts per subreddit, extract title, score, URL, selftext
- **Fallback**: If Reddit API is unavailable, use Agent Reach (Exa search) as backup

### Source 5: Product Hunt

- **Method**: GraphQL API (`https://api.producthunt.com/v2/api/graphql`)
- **Auth**: OAuth2 access token (free for non-commercial, requires app approval)
- **Query**: Today's posts, filter by AI/ML topic tags
- **Fetch**: Top 10 products, extract name, tagline, description, votesCount, URL
- **Fallback**: If API access is denied/unavailable, scrape via Jina Reader (`https://r.jina.ai/https://www.producthunt.com/`) and use LLM to extract structured data from the markdown output

### Source 6: GitHub Trending

- **Method**: Agent Reach (Jina Reader) — `https://r.jina.ai/https://github.com/trending/python?since=daily`
- **Auth**: None (Jina Reader is free)
- **Fallback**: Direct HTML scraping of `github.com/trending/python?since=daily`
- **Fetch**: Top 10 trending Python repos, extract name, description, stars, language, URL

## LLM Processing (OpenAI API)

### Structured Processing

For each fetched item across all sources, call OpenAI to extract:
```json
{
  "one_line_summary": "一句话中文描述",
  "category": "产品/论文/开源/讨论/新闻",
  "relevance": "high/medium/low",
  "key_insight": "核心亮点",
  "tags": ["agent", "LLM", "infra"]
}
```

**Optimization**: Batch items by source, send multiple items in a single prompt to reduce API calls. Use `gpt-4o-mini` for per-item structured processing to minimize cost.

### Executive Summary

After all items are structured, generate a 200-400 word Chinese executive summary:
- Today's top 3 trends
- Most noteworthy items across all sources
- Key themes and connections

**Model**: `gpt-4o` for executive summary, `gpt-4o-mini` for per-item processing (configurable in `.env`)

### Cost Estimate

- Per-item processing (~60 items): ~$0.02/day with `gpt-4o-mini`
- Executive summary (1 call): ~$0.01/day with `gpt-4o`
- Total: ~$0.03/day, ~$1/month

### Timeout & Retry Strategy

- Per-call timeout: 60 seconds
- Max total LLM processing budget: 10 minutes
- Retry: up to 2 times with exponential backoff (1s, 4s)
- If `gpt-4o` fails repeatedly, fall back to `gpt-4o-mini` for executive summary

## PDF Generation (WeasyPrint)

### Template Architecture

```
templates/
├── daily_report.html     # Jinja2 main template
├── styles.css            # Report styling
└── components/
    ├── header.html       # Report header with date/branding
    ├── summary.html      # Executive summary section
    ├── section.html      # Reusable section (per source)
    └── footer.html       # Footer with generation info
```

### Report Structure

Following the reference PDF layout:

```
┌─────────────────────────────────┐
│  AI 认知日报 — 2026-03-18       │  Header
├─────────────────────────────────┤
│  Executive Summary              │  全局总结 (200-400字)
│  今日最值得关注的趋势和亮点      │
├─────────────────────────────────┤
│  📦 新产品 (Product Hunt)       │  Section per source
│  ┌─────────────────────────┐   │  - Table format
│  │ Name │ Tagline │ Votes  │   │  - One-line summary
│  └─────────────────────────┘   │  - Links
├─────────────────────────────────┤
│  🔥 热门讨论 (Hacker News)      │
├─────────────────────────────────┤
│  📰 RSS 精选 (Folo)            │
├─────────────────────────────────┤
│  📄 学术论文 (arXiv)            │
├─────────────────────────────────┤
│  💬 社区动态 (Reddit)           │
├─────────────────────────────────┤
│  ⭐ 开源项目 (GitHub Trending)  │
├─────────────────────────────────┤
│  Generated by AI Daily Digest   │  Footer
│  2026-03-18                     │
└─────────────────────────────────┘
```

### Styling Goals

- Professional, clean, easy to read
- Clear section dividers with icons
- Tables for structured data
- Blockquotes for summaries and insights
- Consistent color scheme (dark headers, light backgrounds)
- Chinese font support (Noto Sans SC — on CI, install via `sudo apt-get install fonts-noto-cjk`; on Windows, bundle font file in `templates/fonts/`)

## Email Delivery (SMTP)

### Implementation

- Python `smtplib` + `email.mime` (standard library)
- Support multiple recipients (comma-separated in config)
- Email body: plain text executive summary
- Attachment: generated PDF file
- Subject: `[AI日报] 2026-03-18 每日认知日报`

### Configuration (`.env`)

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-specific-password
EMAIL_RECIPIENTS=recipient1@example.com,recipient2@example.com
EMAIL_FROM=AI Daily Digest <your-email@gmail.com>
```

## Web UI

### Backend: FastAPI

```python
# api/server.py — Key endpoints
GET  /api/reports              # List generated reports (date, status)
GET  /api/reports/{date}       # Get report details + PDF download
POST /api/reports/generate     # Trigger pipeline manually
GET  /api/sources              # List data sources and status
GET  /api/config               # Get current configuration
PUT  /api/config               # Update configuration
GET  /api/status               # Pipeline run status
```

### Frontend: Next.js

```
web/
├── app/
│   ├── page.tsx               # Dashboard — latest report + stats
│   ├── reports/
│   │   ├── page.tsx           # Report list (calendar/table view)
│   │   └── [date]/page.tsx    # Single report view + PDF embed
│   ├── config/
│   │   └── page.tsx           # Configuration management
│   └── layout.tsx             # Shell layout with sidebar nav
├── components/
│   ├── ReportCard.tsx
│   ├── SourceStatus.tsx
│   ├── PdfViewer.tsx
│   └── ConfigForm.tsx
└── lib/
    └── api.ts                 # API client
```

### Key Features

1. **Dashboard**: Today's report preview, source status indicators, quick stats
2. **Report Browser**: Browse historical reports by date, view/download PDF
3. **Manual Trigger**: Button to trigger pipeline generation on demand
4. **Config Management**: Edit data sources, email recipients, LLM settings
5. **Run History**: View past runs, success/failure status, logs

## Project Structure

```
RSS-Notion/
├── daily-digest.mjs              # Existing Notion flow (preserved)
├── prompt.md                     # Existing prompt (preserved)
├── config.json                   # Existing config (extended)
│
├── main.py                       # New pipeline entry point
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variable template
│
├── sources/                      # Data source modules
│   ├── __init__.py
│   ├── base.py                   # BaseSource abstract class
│   ├── hackernews.py
│   ├── arxiv_source.py
│   ├── reddit.py
│   ├── producthunt.py
│   ├── github_trending.py
│   └── folo.py                   # Queries Notion API for today's Folo articles
│
├── generator/                    # Content generation
│   ├── __init__.py
│   ├── summarizer.py             # OpenAI structured processing + summary
│   └── pdf_builder.py            # WeasyPrint PDF rendering
│
├── templates/                    # Jinja2 HTML/CSS for PDF
│   ├── daily_report.html
│   ├── styles.css
│   └── components/
│
├── delivery/                     # Email
│   ├── __init__.py
│   └── emailer.py
│
├── api/                          # FastAPI backend
│   ├── __init__.py
│   ├── server.py
│   └── routes/
│
├── web/                          # Next.js frontend
│   ├── package.json
│   ├── next.config.js
│   ├── app/
│   └── components/
│
├── output/                       # Generated reports (gitignored)
│   └── 2026-03-18.pdf
│
├── logs/                         # Logs (gitignored)
│
├── run.sh                        # Linux/Mac entry point
├── run.bat                       # Windows entry point
├── .github/
│   └── workflows/
│       └── daily-digest.yml      # GitHub Actions schedule
│
├── docs/
│   ├── design.md                 # Design document (deliverable)
│   └── superpowers/specs/
│
└── README.md                     # Setup & usage guide (deliverable)
```

## Configuration

### Extended `config.json`

```json
{
  "model": "claude-sonnet-4-6",
  "notion": { "..." },
  "schedule": { "..." },
  "folo": { "..." },

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
      "provider": "openai",
      "model": "gpt-4o"
    },
    "pdf": {
      "template": "daily_report",
      "output_dir": "output"
    }
  }
}
```

### `.env.example`

```bash
# OpenAI
OPENAI_API_KEY=sk-...

# Notion (for Folo source — Python reads today's articles from Notion)
NOTION_TOKEN=ntn_...

# Reddit OAuth (create "script" type app at https://www.reddit.com/prefs/apps)
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=ai-daily-digest/1.0

# Product Hunt (apply at https://api.producthunt.com/v2/docs)
PRODUCTHUNT_TOKEN=...

# Email SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
EMAIL_RECIPIENTS=recipient1@example.com,recipient2@example.com
EMAIL_FROM=AI Daily Digest <your-email@gmail.com>

# Existing (for Phase 1: Folo MCP + Claude CLI)
FOLO_SESSION_TOKEN=...
```

## Automation & Scheduling

### Single Command Trigger

```bash
# Full pipeline (Notion + PDF + Email)
./run.sh

# PDF pipeline only (skip Notion)
python main.py

# With options
python main.py --skip-email --skip-notion
python main.py --sources hackernews,arxiv
```

### GitHub Actions

```yaml
# .github/workflows/daily-digest.yml
# NOTE: This runs Phase 2 (Python pipeline) only.
# Phase 1 (Folo + Notion via Claude CLI) requires local execution.
# Folo source will be gracefully skipped on CI.
name: Daily AI Digest
on:
  schedule:
    - cron: '0 0 * * *'  # 08:00 UTC+8
    - cron: '0 10 * * *'  # 18:00 UTC+8
  workflow_dispatch: {}  # Manual trigger

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Install system dependencies (Chinese fonts for PDF)
        run: sudo apt-get install -y fonts-noto-cjk
      - run: pip install -r requirements.txt
      - run: python main.py --skip-notion
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          REDDIT_CLIENT_ID: ${{ secrets.REDDIT_CLIENT_ID }}
          REDDIT_CLIENT_SECRET: ${{ secrets.REDDIT_CLIENT_SECRET }}
          PRODUCTHUNT_TOKEN: ${{ secrets.PRODUCTHUNT_TOKEN }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          EMAIL_RECIPIENTS: ${{ secrets.EMAIL_RECIPIENTS }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
      - uses: actions/upload-artifact@v4
        with:
          name: daily-report-${{ github.run_id }}
          path: output/*.pdf
```

### Cron (Linux/Mac)

```bash
# crontab -e
0 8,18 * * * cd /path/to/RSS-Notion && ./run.sh >> logs/cron.log 2>&1
```

## Data Persistence

### Storage Model

```
output/
├── 2026-03-18/
│   ├── report.pdf              # Generated PDF
│   ├── data.json               # All aggregated + processed items
│   ├── summary.json            # Executive summary + metadata
│   └── run.log                 # Pipeline run log
├── 2026-03-17/
│   └── ...
└── reports.db                  # SQLite metadata (optional, for Web UI)
```

- **`data.json`**: All fetched and LLM-processed items, keyed by source. Serves as the data layer for the Web UI's report detail view.
- **`reports.db`** (SQLite): Tracks run history (date, status, duration, source counts, PDF path). Used by FastAPI endpoints for listing and querying reports. Created lazily on first Web UI start.
- **PDF and JSON files**: Primary storage. The system works without SQLite (CLI mode reads directly from `output/` directories).

## Concurrency Model

- Source fetching uses `asyncio` + `aiohttp` for parallel HTTP requests
- All 5 external sources fetched concurrently; Folo (Notion query) runs as part of the same async batch
- **Exception**: arXiv has a 3-second delay recommendation — handled by `asyncio.sleep(3)` between paginated calls within the arXiv coroutine, without blocking other sources
- LLM batch processing: sequential per-source batches (to avoid OpenAI rate limits), but sources are processed in order without waiting for all sources to complete first — items are processed as each source finishes

## Error Handling

- Each source fetcher is independent — one source failure doesn't block others
- Failed sources are logged and noted in the PDF report ("Source unavailable" with reason)
- LLM API failures: retry up to 2 times with exponential backoff (1s, 4s); fall back to `gpt-4o-mini` if `gpt-4o` keeps failing
- PDF generation failure: log error, skip email, exit with non-zero code
- Email failure: log error, PDF is still saved locally
- **`run.sh` Phase 1 failure**: If `daily-digest.mjs` exits non-zero, log a warning but continue to Phase 2. The Folo source will simply return 0 items (Notion query finds nothing new). This ensures the rest of the digest is still generated.

## .gitignore Updates

The following entries need to be added to `.gitignore`:
```
output/
__pycache__/
*.pyc
.venv/
web/node_modules/
web/.next/
reports.db
```

## Deliverables Mapping

| Requirement | Deliverable |
|---|---|
| Complete project code + README.md | `README.md` with setup, API keys config, run instructions |
| PDF report sample | `output/2026-03-18.pdf` (system-generated) |
| Design document | `docs/design.md` (architecture, tech choices, challenges, scheduling) |
| Extra credit: Web Demo | Next.js + FastAPI web UI |
| Extra credit: Automation | GitHub Actions + cron configs |
| Extra credit: Source quality | 6 sources including personalized Folo RSS |
