from datetime import datetime, timezone

from soliloquy.analysis_store import AnalysisSnapshot
from soliloquy.analyzer import AnalysisResult
from soliloquy.mood_chart import render_mood_trend_svg


def _snapshot(mood_score, created_at=None):
    return AnalysisSnapshot(
        days=1, audience="self",
        created_at=created_at or datetime.now(timezone.utc),
        result=AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=[], mood_score=mood_score),
    )


def test_returns_none_with_fewer_than_two_scored_snapshots():
    assert render_mood_trend_svg([]) is None
    assert render_mood_trend_svg([_snapshot(5)]) is None


def test_returns_none_when_all_snapshots_lack_a_mood_score():
    assert render_mood_trend_svg([_snapshot(None), _snapshot(None)]) is None


def test_returns_svg_with_two_or_more_scored_snapshots():
    svg = render_mood_trend_svg([_snapshot(3), _snapshot(8)])
    assert svg is not None
    assert svg.startswith("<svg")
    assert "<polyline" in svg
    assert svg.count("<circle") == 2


def test_skips_unscored_snapshots_but_still_renders_the_scored_ones():
    svg = render_mood_trend_svg([_snapshot(3), _snapshot(None), _snapshot(8)])
    assert svg is not None
    assert svg.count("<circle") == 2
