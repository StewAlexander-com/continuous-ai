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

TEN PASSES (high SNR, readable — not a syntax-theme port)
---------------------------------------------------------
1. Information architecture. Every console line is content (her words), identity
   (the Aida: label), chrome (hints, brackets, status), or signal (OK / warn).
   Hue is allowed only on identity and signal. Body text stays type on the canvas.
2. Luminance first. Canvas contrast is the reading surface (cream on charcoal,
   ink on paper). Meaning never depends on hue alone: warn is bold in every theme.
3. Palette reduction. Four borrowed Monokai notes, not the syntax set: charcoal,
   cream, cyan (identity), orange (warn); green only for the word OK.
4. One accent. The speaker label is the only chromatic identity. No syntax
   highlighting of replies — coloring the words would be noise.
5. Canvas, not bars. dark / light-color set the terminal default fg/bg (OSC 10/11)
   and restore it on exit. No per-line background stripes.
6. Default is b&w. Shipped theme is type hierarchy (dim chrome, bold warn), no
   hue, no canvas. Color is a choice, not a surprise on upgrade.
7. Choice is explicit. :theme dark | :theme light-color | :theme b&w. Persists
   to config.local.yaml. theme:dark etc. are accepted as names.
8. NO_COLOR / non-TTY win. Pipes, CI, and NO_COLOR stay plain text. FORCE_COLOR
   can still prove SGR in tests without painting the window.
9. One module. dim / warn / ok / colored / speaker_prefix all read the active
   theme. Call sites do not pick hex.
10. Wrap math uses visible width. ANSI on the speaker label must not shift the
    column count, or replies wrap early.

NO_COLOR / non-TTY: if the NO_COLOR env var is set (https://no-color.org) or
stdout isn't a TTY, every helper degrades to plain text — no escape codes. This
is checked lazily so tests and pipes get clean output automatically.
"""
from __future__ import annotations

import atexit
import os
import sys

# --- raw codes (used ONLY inside this module) ---
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# The speaker's name — the one place the reply prefix is defined.
SPEAKER = "Aida"
SPEAKER_PREFIX_PLAIN = f"{SPEAKER}: "

DEFAULT_THEME = "b&w"
THEMES = ("b&w", "dark", "light-color")

# Judicious Monokai borrow: canvas + four semantic notes. Not a syntax port.
# RGB tuples so truecolor SGR and OSC share one source.
_PALETTES: dict[str, dict[str, tuple[int, int, int] | None]] = {
    "b&w": {
        "canvas_bg": None,
        "canvas_fg": None,
        "speaker": None,
        "chrome": None,
        "warn": None,
        "ok": None,
    },
    "dark": {
        "canvas_bg": (39, 40, 34),      # #272822 charcoal
        "canvas_fg": (248, 248, 242),   # #F8F8F2 cream
        "speaker": (102, 217, 239),     # #66D9EF cyan — identity, not alarm
        "chrome": (117, 113, 94),       # #75715E comment gray
        "warn": (253, 151, 31),         # #FD971F orange
        "ok": (166, 226, 46),           # #A6E22E green — the word OK only
    },
    "light-color": {
        "canvas_bg": (250, 248, 243),   # #FAF8F3 warm paper
        "canvas_fg": (39, 40, 34),      # #272822 ink
        "speaker": (10, 108, 128),      # darkened cyan, ~7:1 on paper
        "chrome": (107, 103, 88),       # muted stone
        "warn": (168, 90, 0),           # darkened orange
        "ok": (61, 122, 15),            # darkened green
    },
}

_THEME_ALIASES = {
    "b&w": "b&w",
    "bw": "b&w",
    "b/w": "b&w",
    "black-and-white": "b&w",
    "blackandwhite": "b&w",
    "mono": "b&w",
    "monochrome": "b&w",
    "dark": "dark",
    "light": "light-color",
    "light-color": "light-color",
    "lightcolor": "light-color",
}

_active_theme = DEFAULT_THEME
_canvas_applied = False
_atexit_registered = False


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


def _stdout_is_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def hues_enabled() -> bool:
    """Hue (not dim/bold) — only dark / light-color, and only when color is on."""
    return color_enabled() and _active_theme in ("dark", "light-color")


def parse_theme(raw: str) -> str | None:
    """Map user/config input to a theme name, or None if unknown."""
    t = (raw or "").strip().lower().replace("_", "-")
    t = t.replace(" ", "")
    if t.startswith("theme:"):
        t = t[6:]
    t = t.lstrip(":")
    if t.startswith("theme:"):
        t = t[6:]
    return _THEME_ALIASES.get(t)


def set_theme(name: str | None) -> str:
    """Activate a theme (unknown names fall back to b&w). Returns the name used."""
    global _active_theme
    parsed = parse_theme(name or "") or DEFAULT_THEME
    _active_theme = parsed
    return parsed


def current_theme() -> str:
    return _active_theme


def _palette() -> dict[str, tuple[int, int, int] | None]:
    return _PALETTES.get(_active_theme) or _PALETTES[DEFAULT_THEME]


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _paint(text: str, *, rgb: tuple[int, int, int] | None = None,
           attr: str | None = None) -> str:
    if not color_enabled() or not text:
        return text
    parts: list[str] = []
    if attr == "dim":
        parts.append(_DIM)
    elif attr == "bold":
        parts.append(_BOLD)
    if rgb is not None:
        parts.append(_fg(rgb))
    if not parts:
        return text
    return f"{''.join(parts)}{text}{_RESET}"


def _ensure_canvas_atexit() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(release_canvas)
    _atexit_registered = True


def apply_canvas() -> None:
    """Set terminal default fg/bg for dark / light-color. No-op for b&w / pipes."""
    global _canvas_applied
    release_canvas()
    if not hues_enabled() or not _stdout_is_tty():
        return
    pal = _palette()
    bg, fg = pal.get("canvas_bg"), pal.get("canvas_fg")
    if not bg or not fg:
        return
    try:
        sys.stdout.write(f"\033]11;{_hex(bg)}\007\033]10;{_hex(fg)}\007")
        sys.stdout.flush()
    except Exception:
        return
    _canvas_applied = True
    _ensure_canvas_atexit()


def release_canvas() -> None:
    """Restore the terminal's default fg/bg if we changed them."""
    global _canvas_applied
    if not _canvas_applied:
        return
    try:
        sys.stdout.write("\033]111\007\033]110\007")
        sys.stdout.flush()
    except Exception:
        pass
    _canvas_applied = False


def canvas_applied() -> bool:
    return _canvas_applied


def dim(text: str) -> str:
    """Secondary text (status lines, hints, system notes)."""
    rgb = _palette().get("chrome") if hues_enabled() else None
    if rgb is not None:
        return _paint(text, rgb=rgb)
    return _paint(text, attr="dim")


def warn(text: str) -> str:
    """Honest-read warning (e.g. a :read error) — bold always; hue only in color themes."""
    rgb = _palette().get("warn") if hues_enabled() else None
    return _paint(text, rgb=rgb, attr="bold")


def ok(text: str) -> str:
    """Health-OK. Hue only in dark / light-color; plain type in b&w."""
    rgb = _palette().get("ok") if hues_enabled() else None
    return _paint(text, rgb=rgb)


def colored(text: str, code: str) -> str:
    """Legacy ANSI codes, mapped onto semantic roles so call sites inherit the theme.

    32/92 → ok, 33/93 → warn, 2 → dim. Anything else passes through when color is on.
    """
    if code in ("32", "92"):
        return ok(text)
    if code in ("33", "93"):
        return warn(text)
    if code in ("2",):
        return dim(text)
    return f"\033[{code}m{text}{_RESET}" if color_enabled() else text


def speaker_prefix() -> str:
    """The reply label. Visible width is always len(SPEAKER_PREFIX_PLAIN)."""
    rgb = _palette().get("speaker") if hues_enabled() else None
    if rgb is not None:
        return _paint(f"{SPEAKER}:", rgb=rgb, attr="bold") + " "
    return SPEAKER_PREFIX_PLAIN


def reply_prefix_inline() -> str:
    """Newline + prefix, matching the old '\\nModel: ' call sites."""
    return f"\n{speaker_prefix()}"


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
        self._label = SPEAKER_PREFIX_PLAIN  # visible width only; ANSI lives in speaker_prefix()
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
