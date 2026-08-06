from datetime import date

from soliloquy.tips import TIPS, get_daily_tip


def test_there_are_a_healthy_number_of_tips():
    assert len(TIPS) >= 30


def test_all_tips_are_unique():
    assert len(set(TIPS)) == len(TIPS)


def test_no_tip_is_blank():
    assert all(t.strip() for t in TIPS)


def test_get_daily_tip_returns_a_real_tip_from_the_list():
    assert get_daily_tip(date(2026, 3, 14)) in TIPS


def test_get_daily_tip_is_deterministic_for_the_same_date():
    d = date(2026, 3, 14)
    assert get_daily_tip(d) == get_daily_tip(d)


def test_get_daily_tip_defaults_to_today_without_raising():
    assert get_daily_tip() in TIPS
