# AI Daily Digest Agent — Design Document

## 1. System Architecture

```
                     ┌─────────────────────┐
                     │     CLI / Cron       │
                     │  python main.py      │
                     │  --interests "..."   │
                     └─────────┬───────────┘
                               │
Phase 1: Fetch    ┌────────────┼────────────┐
                  │            │            │
           ┌──────▼──┐  ┌─────▼───┐  ┌────▼─────┐
           │ HN/Reddit│  │ arXiv   │  │ PH/GitHub│  ... (concurrent)
           └──────┬───┘  └─────┬───┘  └────┬─────┘
                  └────────────┼────────────┘
                               │ all_items[]
Phase 2: Interests             ▼
                  ┌─────────────────────────┐
                  │  Load User Interests    │
                  │  (Notion config or CLI) │
                  └────────────┬────────────┘
                               │
Phase 3: Score                 ▼
                  ┌─────────────────────────┐
                  │  LLM Interest Scoring   │
                  │  (1-10 per item, batch) │
                  └────────────┬────────────┘
                               │ scored_items[]
                  ┌────────────┼────────────┐
Phase 4: Write    ▼            │            │
           ┌──────────┐        │            │
           │  Notion   │        │            │
           │  Inbox    │        │            │
           └──────────┘        │            │
Phase 5: PDF                   ▼            │
                  ┌─────────────────┐       │
                  │  PDF Generation │       │
                  │  (WeasyPrint)   │       │
                  └────────┬────────┘       │
Phase 6: Email             │                ▼
                  ┌────────▼────────────────────┐
                  │  SMTP Email (PDF attached)  │
                  └─────────────────────────────┘
```

All sources are fetched concurrently using `asyncio.gather()`. The pipeline is designed to be fault-tolerant: any single source failure is logged and skipped.

## 2. Data Source Selection & Anti-Scraping Strategy

### Why these 5 sources?

The task requires Product Hunt, Hacker News, GitHub Trending, arXiv, and Reddit. Each presents different access challenges:

| Source | API Type | Challenge | Solution |
|--------|----------|-----------|----------|
| **Hacker News** | Public Firebase API | None — fully open REST API | Direct HTTP, filter by AI keywords client-side |
| **arXiv** | Python `arxiv` package | Rate limiting | `run_in_executor` to avoid blocking, built-in retry |
| **GitHub Trending** | No official API | Scraping needed, Cloudflare | Primary: Jina Reader (renders JS). Fallback: direct BS4 parse |
| **Reddit** | PRAW OAuth / RSS | OAuth requires app registration | 3-tier: PRAW → RSS feed (`/.rss`) → Jina Reader |
| **Product Hunt** | GraphQL + Bearer token | Token required, Cloudflare on website | GraphQL with token → Jina fallback. Graceful 0-item on both fail |

### Key decision: Jina Reader as universal fallback

For sources that block scrapers (Product Hunt, GitHub), we use `r.jina.ai/{url}` as a server-side rendering proxy. This avoids the need for headless browsers while still getting JavaScript-rendered content. If Jina is also blocked (Product Hunt's Cloudflare), we gracefully return 0 items rather than crash.

### Additional source: Folo RSS (optional)

When `NOTION_TOKEN` is configured, the agent also reads from the user's Folo RSS aggregator via their Notion inbox database. This extends coverage to any RSS feed the user subscribes to.

## 3. Personalization: Interest-Based Scoring

### The problem with generic aggregation

A naive aggregator dumps 50+ items with no prioritization. The user must manually scan everything. Tools like Feedly AI or Folo already do this.

### Our approach: user-interest scoring

Every item is scored 1-10 against the user's specific interests:

```
Score 10: Directly hits a core research topic
Score 7-9: Highly relevant to stated interests
Score 4-6: Tangentially related
Score 1-3: Not relevant to this user
```

### Two configuration modes

**CLI mode** (zero setup, for demo/interview):
```bash
python main.py --interests "AI Agent, vertical SaaS, LLM cost"
```

**Notion mode** (persistent, for daily use):
The agent reads from a Notion "AI Config" page containing:
- Filtering perspective (e.g., "product manager and investor")
- Long-term research topics (e.g., "AI Agent ecosystem and commercialization")
- Keywords (e.g., "Agent, MCP, PMF, Business Model")
- Designated topic (one-shot: "research X today")
- Supplementary context from existing research database

### Scoring prompt design

The LLM receives:
1. The user's perspective, topics, and keywords
2. Each item's title, description, source, and URL
3. A scoring rubric mapping scores to relevance levels
4. Optional: designated topic gets a +2 score boost

Items scoring >= 7 (configurable threshold) are:
- Included in the PDF report with priority
- Written to the Notion inbox with structured metadata
- Used to generate the executive summary

Items scoring < 7 still appear in the PDF but in a lower section.

## 4. PDF Generation Strategy

### Primary: WeasyPrint

WeasyPrint produces high-quality PDFs from HTML/CSS with proper pagination, headers, and print media support. We use Jinja2 templates + CSS for layout control.

### Fallback: xhtml2pdf

On Windows (where WeasyPrint requires GTK which is hard to install), we automatically fall back to xhtml2pdf. The main challenge was **Chinese font rendering** — xhtml2pdf renders CJK characters as squares by default.

**Solution**: Register system Chinese fonts (SimHei.ttf) with reportlab's `pdfmetrics` API, then patch xhtml2pdf's `DEFAULT_FONT` dict to map the font name. This embeds the font with proper ToUnicode mappings.

### Template design

The PDF template (`templates/daily_report.html` + `styles.css`) uses:
- Dark header with date and stats
- Executive summary with accent border
- Items grouped by source in tables
- Score/tag display per item
- Print-optimized CSS with page break control

## 5. Email Delivery

Standard Python `smtplib` with SMTP/TLS:
- Subject: `[AI日报] YYYY-MM-DD 每日认知日报`
- Body: Executive summary (plain text)
- Attachment: PDF report
- Supports multiple recipients (comma-separated)

Graceful degradation: missing SMTP config logs a warning and continues (pipeline doesn't fail).

## 6. Notion Integration (Extra Feature)

Unlike a one-way pipeline (fetch → output), this agent uses Notion as a **two-way knowledge base**:

- **Read**: User interests from config page, existing research topics
- **Write**: High-scoring items with structured properties (topic, importance, source)
- **Write**: Pipeline run reports for audit trail

This means the user's Notion inbox automatically accumulates curated, tagged, scored content over time — building a personal knowledge base, not just generating disposable reports.

## 7. Automation

### Single command trigger
```bash
python main.py
```

### Scheduled execution
- **Cron**: `0 8 * * * python main.py`
- **GitHub Actions**: `.github/workflows/daily-digest.yml` with `schedule: cron: '0 0 * * *'`
- **Windows**: `run.bat` via Task Scheduler

### CI/CD considerations
The GitHub Actions workflow installs `fonts-noto-cjk` for Chinese PDF rendering on Linux and uses WeasyPrint (GTK available natively on Ubuntu).

## 8. Challenges & Solutions

| Challenge | Solution |
|-----------|----------|
| Product Hunt Cloudflare blocks scrapers | Jina Reader as proxy; graceful 0-item fallback |
| Reddit requires OAuth for full API | 3-tier fallback: PRAW → RSS → Jina |
| Chinese characters render as ■ in PDF | Register font via reportlab + patch xhtml2pdf DEFAULT_FONT |
| WeasyPrint needs GTK on Windows | Auto-detect and fallback to xhtml2pdf |
| LLM API failures | Exponential backoff retry (3 attempts) + fallback to raw items |
| Generic aggregation has no user value | Interest-based scoring with configurable topics |

## 9. What Makes This Different

This is not just an aggregator. The differentiation:

1. **Personalized scoring** — Every item scored against YOUR interests, not generic categories
2. **Notion as knowledge base** — Not disposable reports, but accumulated structured knowledge
3. **Fault-tolerant multi-source** — Every source has fallbacks; partial failures are expected and handled
4. **Two configuration modes** — CLI for quick demo, Notion for persistent daily use
5. **Professional PDF** — WeasyPrint rendering with Chinese support, not markdown-to-PDF
