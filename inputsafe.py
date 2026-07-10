"""
seedling/inputsafe.py — hardened, multi-line console input.

THREAT MODEL
------------
stdin is untrusted. A paste can carry anything: terminal escape sequences that
hijack the cursor/title (or worse on paste-executing terminals), enormous dumps
that exhaust memory or the model context, raw control bytes (NUL/BEL) that
corrupt the terminal or the log, malformed UTF-8, and Unicode "trojan source"
tricks (bidi overrides, zero-width chars) that make text read differently than
it is. This module turns whatever the user pastes/types into a single, bounded,
printable, escape-free string — WITHOUT mangling legitimate code/CSV/Python
(newlines, tabs, and all printable Unicode are preserved).

Design rules:
  - STRIP, don't silently "fix" meaning. The only lossy step is the size cap,
    and that is LOUD (the caller is told exactly how much was dropped).
  - Preserve \\n and \\t and every printable char (Stew pastes code all day).
  - Never raise on weird bytes — decode with errors="replace".
"""
from __future__ import annotations

import re
import unicodedata

# --- limits (protect the sandbox + model context) ---
MAX_CHARS = 100_000     # ~100 KB of typed/pasted text per turn (use :read for files)
MAX_LINES = 2_000

# C0 controls except TAB(09) LF(0A) CR(0D); plus DEL(7F) and C1 (80-9F).
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]"
)
# ANSI / terminal escape sequences (CSI, OSC, and bare ESC-led forms).
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"      # CSI ... cmd
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL/ST
    r"|\x1b[@-Z\\-_]"               # 2-char ESC sequences
    r"|\x1b."                       # any other ESC-led pair (catch-all)
)
# Unicode "trojan source" / spoofing: bidi overrides + zero-width + BOM.
_DANGEROUS_UNICODE = (
    "\u202a\u202b\u202c\u202d\u202e"  # LRE RLE PDF LRO RLO (bidi)
    "\u2066\u2067\u2068\u2069"        # LRI RLI FSI PDI (isolates)
    "\u200b\u200c\u200d\u200e\u200f"  # ZWSP ZWNJ ZWJ LRM RLM
    "\ufeff"                          # BOM / ZWNBSP
)
_DANGEROUS_RE = re.compile("[" + re.escape(_DANGEROUS_UNICODE) + "]")


def sanitize_input(text: str) -> tuple[str, list[str]]:
    """Return (clean_text, notices). Pure; never raises.

    notices is a list of human-readable strings describing anything that was
    altered (truncation, stripped escapes, etc.) — the caller surfaces them so
    the change is never silent.
    """
    notices: list[str] = []
    if text is None:
        return "", notices

    # Defensive: if somehow bytes slipped in, decode leniently.
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")

    # 1) ANSI/terminal escapes — strip first (before control strip eats the ESC).
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
        text = text.replace("\x1b", "")  # any leftover lone ESC
        notices.append("removed terminal escape sequences")

    # 2) Trojan-source / zero-width Unicode.
    if _DANGEROUS_RE.search(text):
        text = _DANGEROUS_RE.sub("", text)
        notices.append("removed hidden/bidirectional Unicode characters")

    # 3) Raw control bytes (keep TAB/LF/CR).
    if _CONTROL_RE.search(text):
        text = _CONTROL_RE.sub("", text)
        notices.append("removed non-printable control characters")

    # 4) Normalize line endings, then NFC (canonical) without touching content.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)

    # 5) Line-count cap.
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        dropped = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES]
        text = "\n".join(lines)
        notices.append(f"truncated to first {MAX_LINES} lines ({dropped} dropped)")

    # 6) Char-count cap (the hard memory/context guard).
    if len(text) > MAX_CHARS:
        dropped = len(text) - MAX_CHARS
        text = text[:MAX_CHARS]
        notices.append(f"truncated to first {MAX_CHARS:,} characters ({dropped:,} dropped)")

    return text, notices


_REPL_YOU_PREFIX = re.compile(r"(?i)^you:\s*(.*)$")


def normalize_repl_input(text: str) -> str:
    """Strip echoed REPL prompt prefixes (``You:``) from a single-line turn.

    Users sometimes paste or re-type the visible prompt; without stripping,
    ``You: :read foo.py`` would miss command dispatch and invite confabulation.
    """
    t = (text or "").strip()
    while True:
        m = _REPL_YOU_PREFIX.match(t)
        if not m:
            break
        t = m.group(1).strip()
    return t


def is_read_command_line(text: str) -> bool:
    """True when the normalized line is a :read command (not plain chat)."""
    t = normalize_repl_input(text).strip().lower()
    return t == ":read" or t.startswith(":read ")


def looks_like_command(first_line: str) -> bool:
    """True if a SINGLE-line input is a REPL command/quit. Multi-line blocks are
    NEVER treated as commands (closes the 'line 2 sneaks :model/exit' hole)."""
    s = normalize_repl_input(first_line).strip().lower()
    return (s in ("exit", "quit", "q", ":q", ":help", ":?", ":setup", ":dispositions",
                  ":model", ":models", ":read", ":more",
                  ":voice chatty", ":voice terse", ":voice normal")
            or s.startswith(":model ") or s.startswith(":models ")
            or s.startswith(":read ")
            or s in (":voice", ":voice on", ":voice off"))


def _drain_buffered_lines(_stdin=None) -> list[str]:
    """Non-blocking peek: return any lines ALREADY buffered on stdin right after
    the first line was read. A multi-line PASTE arrives all at once, so those
    lines are sitting in the buffer; normal typing leaves the buffer empty.
    This is how we get multi-line paste support WITHOUT forcing a blank-line
    submit on every single-line turn. Safe no-op when select() isn't available.
    """
    import sys
    import select
    stdin = _stdin or sys.stdin
    out = []
    try:
        while select.select([stdin], [], [], 0)[0]:
            line = stdin.readline()
            if not line:
                break
            out.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass
    return out


def read_multiline(prompt: str = "You: ", _input=input, _isatty=None,
                   _drain=None):
    r"""Read one logical turn. Single-line by default; pasted blocks come in whole.

    Behavior:
      - Read ONE line and, in the common case, return it immediately
        (NO blank-line-to-submit — that was the v2.6.0 hang regression).
      - If a multi-line PASTE was delivered, the extra lines are already
        buffered on stdin; we drain them in one shot and return the whole block.
      - Empty first line -> "" (caller skips the turn).
      - Command/quit on a single line -> returned as-is for dispatch.
      - EOF (Ctrl-D) on the first line -> EOFError (exit).
      - Non-TTY (piped) input -> read all stdin as one turn; never hang.

    Returns the RAW string (caller runs sanitize_input). Raises EOFError = exit.
    """
    import sys
    if _isatty is None:
        try:
            _isatty = sys.stdin.isatty()
        except Exception:
            _isatty = False
    drain = _drain if _drain is not None else _drain_buffered_lines

    # --- Piped / non-interactive: consume all of stdin as one turn. ---
    if not _isatty:
        data = sys.stdin.read()
        if not data:
            raise EOFError
        return data

    # --- Interactive: read exactly one line, return immediately unless a
    #     paste delivered more lines in the same burst. ---
    first = _input(prompt)            # EOFError propagates -> caller exits
    extra = drain()                   # any buffered paste lines (usually none)
    if not extra:
        # The overwhelmingly common path: a single typed line. Return now.
        return first
    # A multi-line paste: first line + the buffered remainder, as one turn.
    return "\n".join([first] + extra)
