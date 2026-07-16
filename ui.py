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


def terminal_columns(width: int | None = None) -> int:
    """Best-effort console width for wrapping (honors COLUMNS, then OS)."""
    import shutil

    if width is not None:
        return max(40, int(width))
    env = os.environ.get("COLUMNS")
    if env and str(env).isdigit():
        return max(40, int(env))
    try:
        return max(40, shutil.get_terminal_size(fallback=(78, 24)).columns)
    except OSError:
        return 78


def _is_fence_line(line: str) -> bool:
    return line.strip().startswith("```")


def _break_chunk(text: str, avail: int) -> int:
    """Return split index for wrapping; prefers spaces, hard-breaks if needed."""
    if len(text) <= avail:
        return len(text)
    sp = text.rfind(" ", 0, avail + 1)
    if sp > 0:
        return sp
    return max(1, avail)


def _word_break_index(text: str, avail: int) -> int | None:
    """Return a space break index within avail, or None if none yet."""
    if len(text) <= avail:
        return len(text)
    sp = text.rfind(" ", 0, avail + 1)
    return sp if sp > 0 else None


def wrap_plain_lines(
    text: str,
    *,
    indent: int = 0,
    width: int | None = None,
) -> list[str]:
    """Wrap plain multi-line text for status/dim blocks (no speaker prefix)."""
    import textwrap

    cols = terminal_columns(width)
    pad = " " * indent
    inner = max(20, cols - indent)
    out: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        if not raw.strip():
            out.append(pad)
            continue
        chunks = textwrap.wrap(
            raw,
            width=inner,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not chunks:
            out.append(pad + raw)
            continue
        for i, chunk in enumerate(chunks):
            if len(chunk) > inner:
                # URLs / tokens longer than the column — hard-break rather than overflow.
                start = 0
                while start < len(chunk):
                    out.append(pad + chunk[start : start + inner])
                    start += inner
            else:
                out.append(pad + chunk)
    return out or [pad]


def wrap_hint_lines(
    text: str,
    *,
    indent: int = 0,
    width: int | None = None,
    segment_sep: str = " | ",
) -> list[str]:
    """Wrap banner/hint lines; prefer breaks at segment separators (e.g. ' | ')."""
    cols = terminal_columns(width)
    pad = " " * indent
    inner = max(20, cols - indent)
    raw = (text or "").strip()
    if not raw:
        return [pad]
    if segment_sep not in raw or len(raw) <= inner:
        return wrap_plain_lines(raw, indent=indent, width=width)
    parts = [p.strip() for p in raw.split(segment_sep)]
    lines: list[str] = []
    current = ""
    for part in parts:
        piece = part if not current else f"{current}{segment_sep}{part}"
        if len(piece) <= inner:
            current = piece
            continue
        if current:
            lines.append(pad + current)
        current = part
    if current:
        lines.append(pad + current)
    return lines or wrap_plain_lines(raw, indent=indent, width=width)


class ReplyStreamWriter:
    """Stream assistant replies with terminal-aware word wrap.

    Preserves streaming feel: emits as soon as a wrap boundary is safe.
    Code-fenced blocks (``` … ```) keep their line structure; only hard-break
    individual lines that exceed the column width.
    """

    def __init__(self, file=None, *, width: int | None = None):
        self._out = file if file is not None else sys.stdout
        self._cols = terminal_columns(width)
        self._label = speaker_prefix()
        self._cont = " " * len(self._label)
        self._started = False
        self._buf = ""
        self._col = 0
        self._fence = False

    @property
    def started(self) -> bool:
        return self._started

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._ensure_started()
        self._buf += chunk
        self._drain()
        self._out.flush()

    def finish(self) -> None:
        if self._buf:
            if self._fence:
                self._emit_code_line(self._buf)
            else:
                self._emit_segment(self._buf, final=True)
            self._buf = ""
        self._out.flush()

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._out.write(reply_prefix_inline())
        self._started = True
        self._col = len(self._label)

    def _avail(self) -> int:
        return max(1, self._cols - self._col)

    def _write(self, text: str) -> None:
        if not text:
            return
        self._out.write(text)
        if text.endswith("\n"):
            self._col = 0
        elif "\n" in text:
            self._col = len(text.rsplit("\n", 1)[-1])
        else:
            self._col += len(text)

    def _soft_break(self) -> None:
        self._write("\n" + self._cont)
        self._col = len(self._cont)

    def _drain(self) -> None:
        while self._buf:
            nl = self._buf.find("\n")
            if nl >= 0:
                line = self._buf[:nl]
                self._buf = self._buf[nl + 1 :]
                self._emit_line(line)
                self._write("\n")
                continue
            if self._fence:
                return
            avail = self._avail()
            if len(self._buf) <= avail:
                return
            cut = _word_break_index(self._buf, avail)
            if cut is None:
                # Incomplete word — wait for more tokens unless pathologically long.
                if " " not in self._buf and len(self._buf) > self._cols:
                    cut = avail
                else:
                    return
            piece = self._buf[:cut]
            self._buf = self._buf[cut:].lstrip(" ")
            self._write(piece)
            if self._buf:
                self._soft_break()

    def _emit_line(self, line: str) -> None:
        if _is_fence_line(line):
            self._emit_code_line(line)
            self._fence = not self._fence
            return
        if self._fence:
            self._emit_code_line(line)
        else:
            self._emit_segment(line)

    def _emit_code_line(self, line: str) -> None:
        avail = self._avail()
        if len(line) <= avail:
            self._write(line)
            return
        start = 0
        while start < len(line):
            avail = self._avail()
            if start == 0 and len(line) <= avail:
                self._write(line)
                return
            take = min(len(line) - start, avail)
            if take <= 0:
                self._soft_break()
                continue
            self._write(line[start : start + take])
            start += take
            if start < len(line):
                self._soft_break()

    def _emit_segment(self, text: str, *, final: bool = False) -> None:
        remaining = text
        while remaining:
            avail = self._avail()
            if len(remaining) <= avail:
                self._write(remaining)
                return
            cut = _break_chunk(remaining, avail)
            self._write(remaining[:cut])
            remaining = remaining[cut:].lstrip(" ")
            if remaining:
                self._soft_break()
            elif not final:
                return


def format_wrapped_reply(text: str, *, width: int | None = None) -> str:
    """Wrap a complete assistant reply (non-streaming fallback)."""
    import io

    w = ReplyStreamWriter(io.StringIO(), width=width)
    w.feed(text or "")
    w.finish()
    return w._out.getvalue()  # type: ignore[attr-defined]


def summary_field_lines(
    label: str,
    value: str,
    *,
    indent: int = 2,
    width: int | None = None,
    max_chars: int | None = None,
) -> list[str]:
    """Wrap a labeled session-summary field for the console.

    First line: ``  Label: value…``; continuations align under the value column.
    """
    import textwrap

    prefix = " " * indent
    label_part = f"{label}: "
    text = (value or "").strip()
    if max_chars is not None and len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0]
        text = (cut if cut else text[:max_chars]).rstrip() + "…"
    if not text:
        return [f"{prefix}{label_part}"]
    cols = width
    if cols is None:
        cols = terminal_columns()
    value_width = max(20, cols - len(prefix) - len(label_part))
    cont_prefix = prefix + (" " * len(label_part))
    chunks = textwrap.wrap(
        text,
        width=value_width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not chunks:
        return [f"{prefix}{label_part}{text}"]
    lines = [f"{prefix}{label_part}{chunks[0]}"]
    lines.extend(f"{cont_prefix}{c}" for c in chunks[1:])
    return lines


def print_session_end_summary(delta, *, end_summary: dict | None = None) -> None:
    """Print the session-end block; long text fields wrap instead of hard [:80] cuts."""
    print("\n[Session ended]")
    for line in summary_field_lines("Insight logged", getattr(delta, "insight_gained", "") or ""):
        print(line)
    coherence = getattr(delta, "coherence_score", 0.0)
    print(f"  Coherence      : {coherence:.2f}")
    emergent = bool(getattr(delta, "emergent", False))
    if emergent:
        detail = (getattr(delta, "emergent_detail", "") or "").strip()
        emergent_val = detail or "(flagged; no detail captured)"
        for line in summary_field_lines("Emergent", emergent_val):
            print(line)
    else:
        print("  Emergent       : False")
    s = end_summary or {}
    if s:
        print(
            f"  Internal work  : {s.get('deliberations', 0)} deliberation(s)"
            f" · {s.get('contested', 0)} contested"
            f" · {s.get('pruned', 0)} pruned"
        )
        print(
            f"  Beliefs        : {s.get('active_beliefs', 0)} active"
            f" · {s.get('archived_beliefs', 0)} archived (quarantined, revivable)"
        )
    tc = s.get("thread_count")
    if tc is not None:
        thresh = s.get("tuning_threshold_n", 10)
        av = s.get("adapter_version", 0)
        if s.get("tuning_ready"):
            mem = (
                f"  Memory         : {tc}/{thresh} sessions — Tier 1 learning applied"
                f" · Tier 2 available (:tune status)"
            )
        else:
            remaining = max(0, thresh - tc)
            mem = (
                f"  Memory         : {tc}/{thresh} sessions — Tier 1 learning applied"
                f" · {remaining} more for Tier 2"
            )
        print(mem)
        if av > 0:
            print(f"  Adapter        : v{av} (metadata; chat uses base weights until Tier 2 is wired)")
