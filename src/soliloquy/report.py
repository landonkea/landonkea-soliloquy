# ───────────────────────────────────────────────────────────────────
# report.py — turns an AnalysisResult + entries into a readable report
# ───────────────────────────────────────────────────────────────────
# ReportContent is the one shared representation of "what goes in a
# report" -- built once from an AnalysisResult + the (already
# audience-filtered, see cli.py's report_range) entries, then handed
# to whichever format_* renderer the caller wants. Adding a new export
# format later means adding one more format_* function, not touching
# how the report's content is assembled.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape as html_escape

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .analyzer import AnalysisResult
from .entry import Entry

FORMATS = ("text", "markdown", "html", "pdf")


@dataclass
class ReportContent:
    days: int
    audience: str
    entry_count: int
    total_word_count: int
    summary: str
    mood_notes: str
    key_topics: list[str]
    entries: list[tuple[str, str]] = field(default_factory=list)  # (created_at_iso, transcript)


def build_report_content(result: AnalysisResult, entries: list[Entry], audience: str, days: int) -> ReportContent:
    return ReportContent(
        days=days,
        audience=audience,
        entry_count=result.entry_count,
        total_word_count=result.total_word_count,
        summary=result.summary,
        mood_notes=result.mood_notes,
        key_topics=list(result.key_topics),
        entries=[(entry.created_at.isoformat(), entry.transcript) for entry in entries],
    )


def _title(content: ReportContent) -> str:
    return f"Soliloquy report -- last {content.days} days -- audience: {content.audience}"


def format_text(content: ReportContent) -> str:
    lines = [
        _title(content),
        f"{content.entry_count} entries, {content.total_word_count} words",
        "",
        "Summary",
        "-------",
        content.summary,
        "",
        "Mood",
        "----",
        content.mood_notes,
        "",
        "Key topics",
        "----------",
        ", ".join(content.key_topics) if content.key_topics else "(none noted)",
        "",
        "Entries",
        "-------",
    ]
    for created_at, transcript in content.entries:
        lines.append(f"[{created_at}] {transcript}")
    return "\n".join(lines) + "\n"


def format_markdown(content: ReportContent) -> str:
    lines = [
        f"# {_title(content)}",
        "",
        f"**{content.entry_count} entries, {content.total_word_count} words**",
        "",
        "## Summary",
        "",
        content.summary,
        "",
        "## Mood",
        "",
        content.mood_notes,
        "",
        "## Key topics",
        "",
        ", ".join(content.key_topics) if content.key_topics else "(none noted)",
        "",
        "## Entries",
        "",
    ]
    for created_at, transcript in content.entries:
        lines.append(f"- **{created_at}** -- {transcript}")
    return "\n".join(lines) + "\n"


def format_html(content: ReportContent) -> str:
    topics = ", ".join(content.key_topics) if content.key_topics else "(none noted)"
    entries_html = "\n".join(
        f"<li><strong>{html_escape(created_at)}</strong> -- {html_escape(transcript)}</li>"
        for created_at, transcript in content.entries
    ) or "<li>(none)</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html_escape(_title(content))}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 1.5rem; }}
  li {{ margin-bottom: 0.5rem; }}
</style>
</head>
<body>
<h1>{html_escape(_title(content))}</h1>
<p><strong>{content.entry_count} entries, {content.total_word_count} words</strong></p>
<h2>Summary</h2>
<p>{html_escape(content.summary)}</p>
<h2>Mood</h2>
<p>{html_escape(content.mood_notes)}</p>
<h2>Key topics</h2>
<p>{html_escape(topics)}</p>
<h2>Entries</h2>
<ul>
{entries_html}
</ul>
</body>
</html>
"""


def format_pdf(content: ReportContent) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def line(text: str, size: int, bold: bool = False) -> None:
        # new_x/new_y reset the cursor back to the left margin after
        # every cell -- without this, fpdf2 measures the NEXT cell's
        # width from wherever the cursor ended up (not the left
        # margin), which can shrink to zero and raise
        # "Not enough horizontal space to render a single character".
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.multi_cell(0, size * 0.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    line(_title(content), 14, bold=True)
    line(f"{content.entry_count} entries, {content.total_word_count} words", 11)
    pdf.ln(4)

    def section(heading: str, body: str) -> None:
        line(heading, 12, bold=True)
        line(body, 11)
        pdf.ln(2)

    section("Summary", content.summary)
    section("Mood", content.mood_notes)
    section("Key topics", ", ".join(content.key_topics) if content.key_topics else "(none noted)")

    line("Entries", 12, bold=True)
    for created_at, transcript in content.entries:
        line(f"[{created_at}] {transcript}", 10)
        pdf.ln(1)

    return bytes(pdf.output())
