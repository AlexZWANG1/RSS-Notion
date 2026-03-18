# AI Daily Digest Agent

An automated AI agent that aggregates content from 5+ sources, scores items against your personal interests, generates a professional PDF report, and delivers via email — all triggered by a single command.

## Quick Demo

```bash
# Install
pip install -r requirements.txt
cp .env.example .env  # add your OPENAI_API_KEY

# Run with personalized interests
python main.py --interests "AI Agent, LLM inference, SaaS" --skip-email

# Or run with default AI/tech focus
python main.py --skip-email
```

Output: `output/YYYY-MM-DD/report.pdf` + `data.json`

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Data Sources (async)                  │
│  Product Hunt · Hacker News · GitHub · arXiv · Reddit   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │  Interest Scoring   │ ← Your topics & keywords
            │  (LLM, 1-10 scale) │   (CLI flag or Notion config)
            └─────────┬───────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
     📄 PDF      📬 Email     📋 Notion
   (WeasyPrint)  (SMTP+PDF)  (write-back)
```

## Personalization

The agent scores every item against **your interests**, not generic AI topics.

### Option A: CLI flag (quick, no setup)
```bash
python main.py --interests "AI Agent infrastructure, vertical SaaS, LLM cost optimization"
```

### Option B: Notion config page (persistent, auto-synced)
Configure once in your Notion workspace:
- **Long-term topics**: AI Agent ecosystem, SaaS transformation, inference economics...
- **Keywords**: Agent, MCP, PMF, Business Model, Platform...
- **Perspective**: Product manager / Investor / Engineer
- **Designated topic**: "Research X today" (one-shot, auto-cleared)

The agent reads your config before each run and adjusts scoring accordingly.

### How scoring works
```
Item: "unslothai/unsloth" (GitHub Trending, ★975)
  → Your interest: "LLM inference cost optimization"
  → LLM scores: 8/10 (directly relevant to inference economics)
  → Topic: 大模型推理 | Importance: 中
  → Included in report ✅

Item: "best-keyboard-for-coding" (Reddit)
  → Your interest: "AI Agent, SaaS"
  → LLM scores: 2/10 (not relevant)
  → Filtered out ❌
```

## Data Sources

| Source | Method | Fallback | What it gets |
|--------|--------|----------|-------------|
| Product Hunt | GraphQL API | Jina Reader | AI products launched today |
| Hacker News | Firebase API | — | Top AI discussions (filtered by AI keywords) |
| GitHub Trending | Jina Reader + BS4 | Direct scraping | Trending Python repos |
| arXiv | `arxiv` package | — | Papers from cs.AI, cs.CL, cs.LG |
| Reddit | PRAW OAuth | RSS feed | r/LocalLLaMA, r/MachineLearning |
| Folo RSS | Notion API | — | Your curated RSS feeds (optional) |

All sources are fetched **concurrently** via asyncio. Each source has automatic fallback strategies and graceful error handling — a single source failure never blocks the pipeline.

## Setup

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure `.env`
```bash
cp .env.example .env
```

**Required:**
- `OPENAI_API_KEY` — LLM API access
- `OPENAI_BASE_URL` — Custom endpoint (e.g. local proxy). Omit for default OpenAI.

**For email delivery:**
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
- `EMAIL_FROM`, `EMAIL_RECIPIENTS`

**Optional:**
- `NOTION_TOKEN` — Enables Notion config sync + Folo RSS source + write-back
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` — Reddit OAuth (auto-falls back to RSS)
- `PRODUCTHUNT_TOKEN` — Product Hunt API (auto-falls back to Jina)

### 3. Run
```bash
# Full pipeline (all sources + email)
python main.py

# Personalized, skip email
python main.py --interests "AI Agent, robotics, LLM" --skip-email

# Specific sources only
python main.py --skip-email --sources hackernews,github_trending,arxiv

# Skip Notion integration
python main.py --skip-notion
```

## Output

Each run produces `output/{YYYY-MM-DD}/`:

- **`report.pdf`** — Professional PDF with executive summary, items grouped by source, scored and tagged
- **`data.json`** — Structured data with interest scores, topics, and LLM-generated summaries

If Notion is configured, high-scoring items are also written to your Notion inbox database with structured properties (topic, importance, media source).

## Automation

### Cron (daily at 8:00 AM)
```cron
0 8 * * * cd /path/to/RSS-Notion && python main.py
```

### GitHub Actions
See `.github/workflows/daily-digest.yml` — runs daily at 00:00 UTC.

### Windows Task Scheduler
```cmd
run.bat
```

## Tech Stack

- **Python 3.12+** — asyncio, aiohttp for concurrent I/O
- **LLM** — OpenAI API (compatible with any OpenAI-compatible endpoint)
- **PDF** — WeasyPrint (primary) / xhtml2pdf with Chinese font support (fallback)
- **Email** — smtplib SMTP with TLS
- **Notion** — notion-client SDK for read/write
- **API** — FastAPI + uvicorn (optional monitoring dashboard)
- **Frontend** — Next.js 15 + Tailwind CSS (optional)

## Project Structure

```
├── main.py                    # Pipeline orchestrator + CLI
├── config.json                # Source and LLM configuration
├── sources/                   # Async data fetchers
│   ├── hackernews.py
│   ├── arxiv_source.py
│   ├── reddit.py
│   ├── producthunt.py
│   ├── github_trending.py
│   └── folo.py
├── generator/
│   ├── interest_scorer.py     # Personalized interest scoring
│   ├── summarizer.py          # LLM batch processing
│   └── pdf_builder.py         # PDF report generation
├── delivery/
│   ├── emailer.py             # SMTP email sender
│   └── notion_writer.py       # Notion inbox write-back
├── templates/                 # Jinja2 HTML + CSS for PDF
├── api/server.py              # FastAPI backend (optional)
└── web/                       # Next.js frontend (optional)
```

## Design Decisions

See `docs/` for the full design document, including:
- Why each data source was chosen and how anti-scraping is handled
- The interest-based scoring algorithm
- PDF rendering strategy (WeasyPrint vs xhtml2pdf, Chinese font handling)
- Notion as a two-way knowledge base (not just output)
