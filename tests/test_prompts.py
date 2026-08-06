from datetime import date

from soliloquy.prompts import PROMPTS, get_daily_prompt


def test_there_are_over_100_prompts():
    assert len(PROMPTS) > 100


def test_all_prompts_are_unique():
    assert len(set(PROMPTS)) == len(PROMPTS)


def test_no_prompt_is_blank():
    assert all(p.strip() for p in PROMPTS)


def test_get_daily_prompt_returns_a_real_prompt_from_the_list():
    assert get_daily_prompt(date(2026, 3, 14)) in PROMPTS


def test_get_daily_prompt_is_deterministic_for_the_same_date():
    d = date(2026, 3, 14)
    assert get_daily_prompt(d) == get_daily_prompt(d)


def test_get_daily_prompt_differs_across_most_consecutive_days():
    # Not guaranteed for every single pair (mod-wraparound could in
    # principle repeat), but with 100+ prompts, consecutive days
    # should essentially always differ -- a real regression (e.g. a
    # rotation bug that always returns the same prompt) would fail this.
    d1, d2 = date(2026, 3, 14), date(2026, 3, 15)
    assert get_daily_prompt(d1) != get_daily_prompt(d2)


def test_get_daily_prompt_cycles_back_after_len_prompts_days():
    d1 = date(2026, 1, 1)
    d2 = date.fromordinal(d1.toordinal() + len(PROMPTS))
    assert get_daily_prompt(d1) == get_daily_prompt(d2)


def test_get_daily_prompt_defaults_to_today_without_raising():
    assert get_daily_prompt() in PROMPTS
