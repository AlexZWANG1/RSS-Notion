# AI Daily Digest

Automated AI/tech daily digest pipeline — aggregates content from 6 sources, processes with LLM, generates a professional PDF report, and delivers via email.

## Architecture

```
Sources (async) → LLM Processing → PDF Generation → Email Delivery
     ↕                                    ↕
  Notion API                          FastAPI + Next.js UI
```

### Data Sources
| Source | Method | Fallback |
|--------|--------|----------|
| Hacker News | Firebase API | — |
| arXiv | `arxiv` Python package | — |
| GitHub Trending | Jina Reader + BS4 | Direct scraping |
| Reddit | PRAW (OAuth) | RSS feed → Jina |
| Product Hunt | GraphQL API | Jina Reader |
| Folo RSS | Notion API query | — |

### Key Components
- **`sources/`** — Async data fetchers with graceful error handling
- **`generator/summarizer.py`** — LLM batch processing (OpenAI API compatible)
- **`generator/pdf_builder.py`** — PDF generation (WeasyPrint + xhtml2pdf fallback)
- **`delivery/emailer.py`** — SMTP email with PDF attachment
- **`api/server.py`** — FastAPI backend for Web UI
- **`web/`** — Next.js dashboard frontend
- **`main.py`** — CLI pipeline orchestrator

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
cd web && npm install  # for Web UI
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your API keys and SMTP settings
```

Required env vars:
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` — LLM API access
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_RECIPIENTS` — email delivery

Optional:
- `NOTION_TOKEN` — for Folo RSS source
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` — Reddit OAuth (falls back to RSS)
- `PRODUCTHUNT_TOKEN` — Product Hunt API

### 3. Run the pipeline
```bash
# Full pipeline
python main.py

# Skip email / specific sources only
python main.py --skip-email --sources hackernews,github_trending,arxiv

# Skip Notion/Folo source
python main.py --skip-notion
```

### 4. Web UI (optional)
```bash
# Terminal 1: API server
python -m api.server

# Terminal 2: Next.js dev
cd web && npm run dev
```
Open http://localhost:3000

## Output

Reports are saved to `output/{YYYY-MM-DD}/`:
- `report.pdf` — formatted PDF report with executive summary and categorized items
- `data.json` — structured data for programmatic access

## Automation

### Cron (Linux/macOS)
```cron
0 8 * * * cd /path/to/RSS-Notion && python main.py
```

### GitHub Actions
See `.github/workflows/daily-digest.yml` — runs daily at 00:00 UTC.

## Tech Stack
- **Python 3.12+** — asyncio, aiohttp
- **LLM** — OpenAI API (compatible with local proxies like EasyCIL)
- **PDF** — WeasyPrint (primary) / xhtml2pdf (Windows fallback)
- **Email** — Python smtplib SMTP
- **API** — FastAPI + uvicorn
- **Frontend** — Next.js 15 + Tailwind CSS
- **Existing** — Node.js daily-digest.mjs + Claude CLI + Notion MCP (Phase 1)
