from datetime import datetime, timezone

from soliloquy.analyzer import AnalysisResult
from soliloquy.entry import Entry
from soliloquy.report import build_report_content, format_html, format_markdown, format_pdf, format_text


def _sample_content():
    result = AnalysisResult(
        entry_count=1, total_word_count=4, summary="A real summary.",
        mood_notes="Steady, no big swings.", key_topics=["work", "sleep"],
    )
    entries = [Entry(transcript="An entry to show", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))]
    return build_report_content(result, entries, audience="partner", days=7)


def test_build_report_content_carries_over_all_fields():
    content = _sample_content()

    assert content.entry_count == 1
    assert content.total_word_count == 4
    assert content.summary == "A real summary."
    assert content.mood_notes == "Steady, no big swings."
    assert content.key_topics == ["work", "sleep"]
    assert content.audience == "partner"
    assert content.days == 7
    assert content.entries == [("2026-01-01T00:00:00+00:00", "An entry to show")]


def test_format_text_includes_all_sections():
    text = format_text(_sample_content())

    assert "audience: partner" in text
    assert "A real summary." in text
    assert "Steady, no big swings." in text
    assert "work, sleep" in text
    assert "An entry to show" in text


def test_format_markdown_uses_real_markdown_headings():
    md = format_markdown(_sample_content())

    assert "# Soliloquy report" in md
    assert "## Summary" in md
    assert "A real summary." in md
    assert "## Entries" in md
    assert "- **2026-01-01T00:00:00+00:00** -- An entry to show" in md


def test_format_html_escapes_content_and_includes_all_sections():
    content = build_report_content(
        AnalysisResult(entry_count=1, total_word_count=2, summary="<script>bad</script>", mood_notes="fine", key_topics=[]),
        [Entry(transcript="ok")], audience="self", days=1,
    )

    html = format_html(content)

    assert "<script>bad</script>" not in html  # escaped, not injected raw
    assert "&lt;script&gt;" in html
    assert "<h1>" in html
    assert "Entries" in html


def test_format_pdf_produces_real_nonempty_pdf_bytes():
    pdf_bytes = format_pdf(_sample_content())

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500
