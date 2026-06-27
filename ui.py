"""
seedling/ui.py — one themed source of truth for console styling + Aida's voice.

WHY THIS EXISTS
---------------
Two channels were telling different stories. The prose channel (_GUARD_TEXT,
the voice line) insists Aida is a named, continuous presence. The visual channel
labeled every reply "Model:" and scattered raw ANSI escapes (\\033[2m ...) across
seedling.py and voice.py with no NO_COLOR support. This module makes the visual
channel tell the same truth: Aida has a name, and her styling is consistent,
centralized, and respects the user's terminal preferences.

NO_COLOR / non-TTY: if the NO_COLOR env var is set (https://no-color.org) or
stdout isn't a TTY, every helper degrades to plain text — no escape codes. This
is checked lazily so tests and pipes get clean output automatically.
"""
from __future__ import annotations

import os
import sys

# --- raw codes (used ONLY inside this module) ---
_DIM = "\033[2m"
_RESET = "\033[0m"
_YELLOW = "\033[33m"

# The speaker's name — the one place the reply prefix is defined.
SPEAKER = "Aida"


def color_enabled() -> bool:
    """True only when it's safe/wanted to emit ANSI. Honors NO_COLOR and TTY."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):  # explicit override for CI/demos
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def dim(text: str) -> str:
    """Dim/secondary text (status lines, hints, system notes)."""
    return f"{_DIM}{text}{_RESET}" if color_enabled() else text


def warn(text: str) -> str:
    """Honest-read warning (e.g. a :read error) — yellow, no turn taken."""
    return f"{_YELLOW}{text}{_RESET}" if color_enabled() else text


def colored(text: str, code: str) -> str:
    """Arbitrary color code (e.g. status-driven). code like '32' or '2'."""
    return f"\033[{code}m{text}{_RESET}" if color_enabled() else text


def speaker_prefix() -> str:
    """The reply label. Was a hardcoded 'Model: ' on every turn; now it's her
    name, defined once. Kept un-dimmed so the speaker reads clearly."""
    return f"{SPEAKER}: "


def reply_prefix_inline() -> str:
    """Newline + prefix, matching the old '\\nModel: ' call sites."""
    return f"\n{SPEAKER}: "


# --- screen control ---
# These emit terminal control codes, NOT color. They must run whenever we
# previously wrote an in-place line (spinner/progress), regardless of NO_COLOR,
# or the animation line is left on screen. They are still no-ops on a non-TTY
# ONLY for the progress case where nothing was redrawn; the spinner's erase is
# unconditional because it always pairs with a \r write.
def clear_line() -> str:
    r"""Carriage-return + clear-to-end-of-line for progress redraws."""
    return "\r\033[K"


def clear_full_line() -> str:
    r"""Carriage-return + clear-ENTIRE-line. Used to erase the thinking spinner
    on stop; must always emit so the animation never lingers."""
    return "\r\033[2K"
