# ───────────────────────────────────────────────────────────────────
# prompts.py — journaling prompts, rotated one per day
# ───────────────────────────────────────────────────────────────────
# The point is reducing "blank page" hesitation before recording or
# typing an entry -- something to react to instead of staring at an
# empty textarea. Rotation is deterministic by calendar date (day-of-
# year, mod the list length), not random per page load -- the same
# prompt should show all day, and the sequence should be reproducible
# (so "what was yesterday's prompt" has a real, stable answer), not
# reshuffled on every visit.
#
# 120 prompts means the cycle repeats every ~4 months rather than
# every ~2 weeks -- long enough that the repetition itself isn't the
# first thing anyone notices.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date

PROMPTS: list[str] = [
    "What's on your mind right now, before you try to organize it into anything tidy?",
    "What moment from today do you keep coming back to?",
    "What are you avoiding thinking about?",
    "What's something you did today that you're proud of, even a small thing?",
    "Who did you think about today, and why?",
    "What's weighing on you right now?",
    "What made you laugh recently?",
    "What's a conversation you wish had gone differently?",
    "What are you looking forward to?",
    "What's something you're grateful for today that you'd normally overlook?",
    "What did your body feel like today -- tired, tense, energized, something else?",
    "What's a decision you're putting off?",
    "What would you tell a friend who was going through what you're going through right now?",
    "What's something you learned about yourself this week?",
    "What's frustrating you right now?",
    "What's a small win from today?",
    "What do you wish someone had asked you today?",
    "What's a fear you haven't said out loud?",
    "What surprised you today?",
    "What's something you did today purely because you wanted to, not because you had to?",
    "What's a pattern you've noticed in yourself lately?",
    "What are you holding onto that you could probably let go of?",
    "What's something you're looking forward to letting go of?",
    "Describe today in exactly three words, then explain why those three.",
    "What's something you needed today that you didn't get?",
    "What's a memory that came up unprompted today?",
    "What's something you're curious about right now?",
    "Who would you like to talk to but haven't reached out to?",
    "What's a boundary you set today, or wish you had?",
    "What's something you're proud of that no one else noticed?",
    "What's a habit you'd like to change?",
    "What's something you did today that felt like just going through the motions?",
    "What's a compliment you received recently that you haven't let yourself believe?",
    "What's something you're anxious about that, written down, might look smaller?",
    "What's a place you'd like to be right now instead of here?",
    "What's something that annoyed you today that probably won't matter in a week?",
    "What's a risk you took recently, big or small?",
    "What's something you keep meaning to do but haven't?",
    "What's a question you've been avoiding asking yourself?",
    "What's something that felt heavy today, and what would make it feel lighter?",
    "What's a small kindness you noticed today -- given or received?",
    "What's something you did today that your younger self would be surprised by?",
    "What's a belief about yourself you're not sure is still true?",
    "What's something you wish you'd said today?",
    "What's a good thing that happened today that you almost didn't notice?",
    "What's something you're comparing yourself to right now, and is it fair?",
    "What's a physical sensation you're aware of right now -- and what might it be telling you?",
    "What's something you did today for someone else?",
    "What's a worry that's been on repeat lately?",
    "What's something you're excited about that you haven't told anyone?",
    "What's a mistake you made recently, and what did it actually cost you?",
    "What's something you did today that you'd do differently if you could?",
    "What's a version of today that would have felt better, even slightly?",
    "What's something you're tired of explaining to people?",
    "What's a question someone asked you recently that you're still thinking about?",
    "What's something you noticed about someone else today?",
    "What's a feeling you had today that you didn't have words for at the time?",
    "What's something you did today that felt effortless?",
    "What's a thought that's been looping today?",
    "What's something you're trying to convince yourself of right now?",
    "What's a moment today where you felt genuinely present?",
    "What's something you wish you understood better about yourself?",
    "What's a comparison between how you feel now versus how you felt this morning?",
    "What's something you're carrying that isn't actually yours to carry?",
    "What's a small thing that went wrong today, and how did you handle it?",
    "What's something you're hoping will change soon?",
    "What's a piece of advice you've been given that you haven't taken yet?",
    "What's something today reminded you of from your past?",
    "What's a question you'd ask your future self?",
    "What's something you did today just to cope, and did it work?",
    "What's a relationship in your life that's on your mind right now?",
    "What's something you're avoiding saying to someone?",
    "What's a thing you did today that took more energy than it should have?",
    "What's something you noticed yourself doing on autopilot today?",
    "What's a hope you have for tomorrow?",
    "What's something you're disappointed about, even if it seems small?",
    "What's a strength you used today, maybe without realizing it?",
    "What's something you're still figuring out about a situation you're in?",
    "What's a moment today where you felt out of place?",
    "What's something you did today that aligned with who you want to be?",
    "What's a question you haven't let yourself ask about your own life?",
    "What's something you're pretending not to care about?",
    "What's a small routine today that grounded you?",
    "What's something you're bracing yourself for?",
    "What's a thing you did today that you'd want someone to know about?",
    "What's something you felt but didn't express today?",
    "What's a way today was different from a typical day?",
    "What's something you noticed about your own reaction to something today?",
    "What's a thing you're trying not to think about right now?",
    "What's something you did today that took courage, even a little?",
    "What's a piece of your day you'd like to replay, and why?",
    "What's something you're unsure how you feel about?",
    "What's a way you took care of yourself today, or didn't?",
    "What's something you noticed in your environment today that you'd usually miss?",
    "What's a thing someone said today that stuck with you?",
    "What's something you're proud of yourself for not doing today?",
    "What's a question about your future that's been sitting with you?",
    "What's something you did today that felt like progress, even if small?",
    "What's a feeling you've been sitting with for longer than usual?",
    "What's something you did today that you'd call an act of self-respect?",
    "What's a version of yourself from a year ago you'd like to check in with?",
    "What's something you're processing that you haven't fully named yet?",
    "What's a thing today that made you feel more like yourself?",
    "What's something you did today that you're still deciding how you feel about?",
    "What's a piece of unfinished business on your mind?",
    "What's something you noticed about how you talked to yourself today?",
    "What's a way today tested your patience?",
    "What's something you're grateful you don't have to deal with anymore?",
    "What's a thing you did today that you'd want to remember a year from now?",
    "What's something you're hoping to understand better about someone in your life?",
    "What's a way you showed up for yourself today?",
    "What's something today that felt unresolved?",
    "What's a thing you noticed feeling different about compared to last week?",
    "What's something you're quietly hopeful about?",
    "What's a moment today you'd like to sit with a little longer?",
    "What do you need right now that you haven't said out loud, even to yourself?",
]


def get_daily_prompt(today: date | None = None) -> str:
    """Deterministic by calendar date -- same prompt all day, every
    day (not random per request), cycling through PROMPTS in order."""
    today = today or date.today()
    return PROMPTS[today.toordinal() % len(PROMPTS)]
