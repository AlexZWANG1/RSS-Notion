"""PDF report generator — WeasyPrint with xhtml2pdf fallback."""

import os
import re
from datetime import date
from pathlib import Path
from collections import OrderedDict

from jinja2 import Environment, FileSystemLoader

from sources.models import ProcessedItem, SourceResult


# Canonical section ordering and display config
SECTION_CONFIG = OrderedDict([
    ("Product Hunt",     {"icon": "[PH]",  "title": "Product Hunt"}),
    ("Hacker News",      {"icon": "[HN]",  "title": "Hacker News"}),
    ("RSS精选",          {"icon": "[RSS]", "title": "RSS精选 (Folo)"}),
    ("arXiv",            {"icon": "[Ax]",  "title": "arXiv"}),
    ("Reddit",           {"icon": "[Rd]",  "title": "Reddit"}),
    ("GitHub Trending",  {"icon": "[GH]",  "title": "GitHub Trending"}),
])


def _normalize_source(name: str) -> str:
    """Normalize source name — map r/... subreddits to Reddit."""
    if name and name.startswith("r/"):
        return "Reddit"
    return name


def _render_pdf_weasyprint(html_content: str, templates_dir: str, pdf_path: str) -> None:
    """Render PDF using WeasyPrint (requires GTK/Pango)."""
    from weasyprint import HTML
    HTML(string=html_content, base_url=templates_dir).write_pdf(pdf_path)


def _find_chinese_font() -> str | None:
    """Find a Chinese font file on the system."""
    candidates = []
    # Windows fonts — prefer .ttf over .ttc for better compatibility
    win_fonts = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    if os.path.isdir(win_fonts):
        for name in ("simhei.ttf", "NotoSansSC-VF.ttf", "msyh.ttc", "simsun.ttc"):
            p = os.path.join(win_fonts, name)
            if os.path.isfile(p):
                candidates.append(p)
    # Linux fonts
    for d in ("/usr/share/fonts", "/usr/local/share/fonts"):
        if os.path.isdir(d):
            for root, _, files in os.walk(d):
                for f in files:
                    if "notosanssc" in f.lower() or "notosanscjk" in f.lower() or "wqy" in f.lower():
                        candidates.append(os.path.join(root, f))
    return candidates[0] if candidates else None


def _register_chinese_font() -> str:
    """Register a Chinese font with reportlab and return the font family name."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = _find_chinese_font()
    if not font_path:
        return "Helvetica"

    font_name = "ChineseFont"
    try:
        if font_path.endswith(".ttc"):
            # TTC (TrueType Collection) — use subfont index 0
            pdfmetrics.registerFont(TTFont(font_name, font_path, subfontIndex=0))
        else:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        return font_name
    except Exception:
        return "Helvetica"


def _render_pdf_xhtml2pdf(html_content: str, templates_dir: str, pdf_path: str) -> None:
    """Render PDF using xhtml2pdf (pure-Python fallback)."""
    from xhtml2pdf import pisa
    from xhtml2pdf.default import DEFAULT_FONT

    # Register Chinese font with reportlab and patch xhtml2pdf's font map
    font_name = _register_chinese_font()
    if font_name != "Helvetica":
        DEFAULT_FONT[font_name.lower()] = font_name

    # xhtml2pdf needs the CSS inlined; it cannot follow <link> tags reliably.
    css_path = Path(templates_dir) / "styles.css"
    if css_path.exists():
        css_text = css_path.read_text(encoding="utf-8")
        # Remove @import and :root blocks (unsupported by xhtml2pdf)
        css_text = re.sub(r"@import\s+url\([^)]*\)\s*;", "", css_text)
        css_text = re.sub(r":root\s*\{[^}]*\}", "", css_text)

        # Override body font to use registered Chinese font
        if font_name != "Helvetica":
            css_text = css_text.replace(
                'font-family: "Noto Sans SC", "Microsoft YaHei", "SimHei", sans-serif;',
                f'font-family: {font_name};',
            )

        # Replace the <link> tag with inline <style>
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
        grouped[src].append({
            "title": item.original.title,
            "url": item.original.url,
            "source_name": item.original.source_name,
            "one_line_summary": item.one_line_summary,
            "tags": item.tags,
            "score": item.original.score,
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
                "icon": "[+]",
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

    # Try WeasyPrint first, fall back to xhtml2pdf
    try:
        _render_pdf_weasyprint(html_content, templates_dir, pdf_path)
    except (OSError, ImportError, Exception) as exc:
        if "cannot load library" in str(exc) or isinstance(exc, (OSError, ImportError)):
            _render_pdf_xhtml2pdf(html_content, templates_dir, pdf_path)
        else:
            raise

    return pdf_path
