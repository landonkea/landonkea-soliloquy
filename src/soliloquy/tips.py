# ───────────────────────────────────────────────────────────────────
# tips.py — encouraging, practical mental-health tips, rotated daily
# ───────────────────────────────────────────────────────────────────
# Same rotation pattern as prompts.py (deterministic by calendar date,
# not random per page load), shown on pages OTHER than New Entry --
# that page already has its own rotating journaling prompt, and two
# different rotating text boxes on one page would be visual clutter,
# not double the value.
#
# Deliberately a mix of concrete, practical techniques (grounding,
# breathing, DBT/CBT-style skills) and genuine encouragement -- not
# just affirmations. Avoids generic "just think positive" toxic-
# positivity phrasing; several tips explicitly validate that a hard
# day is allowed to just be hard.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date

TIPS: list[str] = [
    "Try the 5-4-3-2-1 grounding technique: name 5 things you can see, 4 you can touch, 3 you can hear, 2 you can smell, 1 you can taste.",
    "You don't have to feel okay to be doing okay. Showing up today counts, even if it didn't feel good.",
    "A slow exhale (longer than your inhale) tells your nervous system it's safe to calm down. Try 4 counts in, 6 counts out.",
    "Progress isn't always visible day to day. Zoom out before deciding nothing's changing.",
    "If today was hard, it's allowed to just be hard. You don't owe anyone a silver lining.",
    "Notice one thing your body did well for you today -- carried you somewhere, let you rest, got you through.",
    "Naming a feeling out loud (even just to yourself) can take some of its intensity down a notch.",
    "You're allowed to be proud of something small. Small isn't the same as unimportant.",
    "If your inner voice is being harsher to you than it would be to a friend, that's worth noticing.",
    "Drinking a glass of water and stepping outside for two minutes counts as taking care of yourself.",
    "You made it through every hard day you've had so far. That's real, not a platitude.",
    "It's okay to need more rest than you planned for. Rest isn't a reward you have to earn.",
    "One unfinished task doesn't erase everything you did finish today.",
    "If you're comparing your behind-the-scenes to someone else's highlight reel, that's not a fair comparison.",
    "Try placing a hand on your chest and just noticing your heartbeat for 30 seconds. It's a small, real anchor.",
    "Asking for help is a skill, not a failure.",
    "You get to change your mind about what you need today, even from what you needed this morning.",
    "Being gentle with yourself isn't the same as letting yourself off the hook -- it's what actually makes change sustainable.",
    "A setback is information, not a verdict on who you are.",
    "You don't have to have it figured out today. Some things resolve on their own timeline.",
    "If you did the bare minimum today, the bare minimum was still something.",
    "Boundaries aren't unkind. They're what make relationships sustainable.",
    "Noticing you're being hard on yourself is already a step toward not being.",
    "You are allowed to take up space with your feelings, even the inconvenient ones.",
    "A short walk changes your physical state, which can shift your mental state too -- even five minutes counts.",
    "The fact that something is still hard doesn't mean you're doing it wrong.",
    "You're not behind. There's no universal schedule you're supposed to be keeping pace with.",
    "Letting yourself feel something fully is often faster than trying to push past it.",
    "You get to define what a good day looks like for you -- it doesn't have to match anyone else's.",
    "Rest is productive. Your nervous system doesn't know the difference between being lazy and recovering.",
    "If you're waiting to feel motivated before you start, try starting first -- motivation often follows action, not the other way around.",
    "A single good conversation can be enough to make a hard week feel more bearable.",
    "You're allowed to outgrow coping mechanisms that used to work and need new ones.",
    "Not every thought you have is true just because you thought it.",
    "Consistency beats intensity. A small thing repeated matters more than one big thing done once.",
    "You can hold two true things at once: this is hard, and you are handling it.",
    "Giving yourself credit doesn't require waiting until things are perfect.",
    "It's okay to protect your energy from people or situations that consistently drain it.",
    "The version of you a year ago would probably be proud of how far you've come, even the messy parts.",
    "Sometimes the most productive thing you can do is nothing, on purpose, without guilt.",
]


def get_daily_tip(today: date | None = None) -> str:
    """Deterministic by calendar date -- same tip all day, cycling
    through TIPS in order, same pattern as prompts.get_daily_prompt()."""
    today = today or date.today()
    return TIPS[today.toordinal() % len(TIPS)]
