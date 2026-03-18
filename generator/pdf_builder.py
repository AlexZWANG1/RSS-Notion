"""PDF report generator — Playwright (Chromium) with xhtml2pdf fallback."""

import os
import re
from datetime import date
from pathlib import Path
from collections import OrderedDict

from jinja2 import Environment, FileSystemLoader

from sources.models import ProcessedItem, SourceResult


# Canonical section ordering and display config
SECTION_CONFIG = OrderedDict([
    ("Product Hunt",     {"icon": "PH",  "title": "Product Hunt"}),
    ("Hacker News",      {"icon": "HN",  "title": "Hacker News"}),
    ("RSS精选",          {"icon": "RSS", "title": "RSS精选 (Folo)"}),
    ("arXiv",            {"icon": "Ax",  "title": "arXiv"}),
    ("Reddit",           {"icon": "Rd",  "title": "Reddit"}),
    ("GitHub Trending",  {"icon": "GH",  "title": "GitHub Trending"}),
])

# Map content_type to CSS class suffix
_CONTENT_TYPE_CLASS = {
    "新闻": "news",
    "深度分析": "analysis",
    "技术报告": "report",
    "博客/视频": "blog",
    "开源项目": "opensource",
}


def _normalize_source(name: str) -> str:
    """Normalize source name — map r/... subreddits to Reddit."""
    if name and name.startswith("r/"):
        return "Reddit"
    return name


def _render_pdf_playwright(html_content: str, pdf_path: str) -> None:
    """Render PDF using Playwright Chromium — full CSS3 support."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_content, wait_until="networkidle")
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={
                "top": "16mm",
                "bottom": "16mm",
                "left": "14mm",
                "right": "14mm",
            },
        )
        browser.close()


def _render_pdf_xhtml2pdf(html_content: str, templates_dir: str, pdf_path: str) -> None:
    """Render PDF using xhtml2pdf (pure-Python fallback)."""
    from xhtml2pdf import pisa
    from xhtml2pdf.default import DEFAULT_FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Try to register a Chinese font
    font_name = "Helvetica"
    win_fonts = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    for name in ("simhei.ttf", "msyh.ttc", "simsun.ttc"):
        fpath = os.path.join(win_fonts, name)
        if os.path.isfile(fpath):
            try:
                if fpath.endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont("ChineseFont", fpath, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont("ChineseFont", fpath))
                font_name = "ChineseFont"
                break
            except Exception:
                pass

    if font_name != "Helvetica":
        DEFAULT_FONT[font_name.lower()] = font_name

    # Inline the CSS
    css_path = Path(templates_dir) / "styles.css"
    if css_path.exists():
        css_text = css_path.read_text(encoding="utf-8")
        css_text = re.sub(r"@import\s+url\([^)]*\)\s*;", "", css_text)
        css_text = re.sub(r":root\s*\{[^}]*\}", "", css_text)
        if font_name != "Helvetica":
            css_text = re.sub(
                r'font-family:[^;]+;',
                f'font-family: {font_name};',
                css_text,
                count=1,
            )
        html_content = html_content.replace(
            '<link rel="stylesheet" href="styles.css">',
            f"<style>{css_text}</style>",
        )

    with open(pdf_path, "wb") as f:
        status = pisa.CreatePDF(html_content, dest=f, encoding="utf-8")
        if status.err:
            raise RuntimeError(f"xhtml2pdf conversion failed with {status.err} errors")


def build_pdf(
    source_results: list[SourceResult],
    processed_items: list[ProcessedItem],
    executive_summary: str,
    output_dir: str = "output",
    report_date: str | None = None,
) -> str:
    """Render the daily digest as a PDF and return the file path."""

    report_date = report_date or date.today().strftime("%Y-%m-%d")

    # ── Group items by normalised source ──
    grouped: dict[str, list[dict]] = {key: [] for key in SECTION_CONFIG}

    for item in processed_items:
        src = _normalize_source(item.original.source_name)
        if src not in grouped:
            grouped[src] = []

        content_type = getattr(item, "content_type", "") or ""
        grouped[src].append({
            "title": item.original.title,
            "url": item.original.url,
            "source_name": item.original.source_name,
            "one_line_summary": item.one_line_summary,
            "tags": item.tags,
            "score": item.original.score,
            "content_type": content_type,
            "content_type_class": _CONTENT_TYPE_CLASS.get(content_type, "news"),
        })

    # ── Build error lookup from source results ──
    source_errors: dict[str, str] = {}
    for sr in source_results:
        norm = _normalize_source(sr.source_name)
        if sr.error:
            source_errors[norm] = sr.error

    # ── Assemble section list in canonical order ──
    sections: list[dict] = []
    seen_sources = set()

    for key, cfg in SECTION_CONFIG.items():
        seen_sources.add(key)
        sections.append({
            "icon": cfg["icon"],
            "title": cfg["title"],
            "entries": grouped.get(key, []),
            "error": source_errors.get(key),
        })

    # Include any extra sources not in the canonical list
    for key in grouped:
        if key not in seen_sources:
            sections.append({
                "icon": "+",
                "title": key,
                "entries": grouped[key],
                "error": source_errors.get(key),
            })

    total_items = sum(len(s["entries"]) for s in sections)
    active_sources = sum(1 for s in sections if s["entries"])

    # ── Render HTML via Jinja2 ──
    templates_dir = str(Path(__file__).resolve().parent.parent / "templates")
    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template("daily_report.html")

    html_content = template.render(
        date=report_date,
        executive_summary=executive_summary,
        sections=sections,
        total_items=total_items,
        active_sources=active_sources,
    )

    # ── Generate PDF ──
    out_path = Path(output_dir) / report_date
    out_path.mkdir(parents=True, exist_ok=True)
    pdf_path = str(out_path / "report.pdf")

    # Try Playwright first, fall back to xhtml2pdf
    try:
        _render_pdf_playwright(html_content, pdf_path)
    except Exception:
        _render_pdf_xhtml2pdf(html_content, templates_dir, pdf_path)

    return pdf_path
