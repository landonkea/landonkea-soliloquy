# ───────────────────────────────────────────────────────────────────
# mood_chart.py, a tiny inline-SVG line chart for mood_score over time
# ───────────────────────────────────────────────────────────────────
# Server-rendered on purpose, same as every other page in this app
# (see web/app.py's module docstring on "no separate JS build") --
# this returns a plain SVG string the Analysis page drops in with
# `{{ mood_chart_svg | safe }}`, not a client-side charting library.
# Deliberately minimal: one polyline, no axes/legend/library, this is
# "here's how this has been trending," not a dashboard.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from soliloquy.analysis_store import AnalysisSnapshot

_WIDTH = 640
_HEIGHT = 120
_PADDING = 12


def render_mood_trend_svg(snapshots: list[AnalysisSnapshot]) -> str | None:
    """snapshots should be in chronological order (oldest first) --
    callers typically have them newest-first (see
    AnalysisSnapshotStore.recent), so reverse before calling this.
    Returns None if there are fewer than 2 scored snapshots -- a
    single point, or none, isn't a "trend" worth drawing."""
    scored = [s for s in snapshots if s.result.mood_score is not None]
    if len(scored) < 2:
        return None

    plot_width = _WIDTH - 2 * _PADDING
    plot_height = _HEIGHT - 2 * _PADDING
    step = plot_width / (len(scored) - 1)

    def y_for(score: int) -> float:
        # mood_score is 1-10 -- map 10 (great) to the top, 1 (rough) to
        # the bottom, same orientation a reader expects from a "how
        # I've been doing" chart.
        return _PADDING + plot_height * (1 - (score - 1) / 9)

    points = " ".join(
        f"{_PADDING + i * step:.1f},{y_for(s.result.mood_score):.1f}" for i, s in enumerate(scored)
    )
    dots = "".join(
        f'<circle cx="{_PADDING + i * step:.1f}" cy="{y_for(s.result.mood_score):.1f}" r="3" fill="var(--accent)"/>'
        for i, s in enumerate(scored)
    )

    return (
        f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" width="100%" height="{_HEIGHT}" '
        'role="img" aria-label="Mood trend over time">'
        f'<polyline points="{points}" fill="none" stroke="var(--accent)" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        f"{dots}"
        "</svg>"
    )
