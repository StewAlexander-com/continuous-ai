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


def looks_like_command(first_line: str) -> bool:
    """True if a SINGLE-line input is a REPL command/quit. Multi-line blocks are
    NEVER treated as commands (closes the 'line 2 sneaks :model/exit' hole)."""
    s = first_line.strip().lower()
    return (s in ("exit", "quit", "q", ":q", ":model", ":read", ":more")
            or s.startswith(":model ") or s.startswith(":read "))


def read_multiline(prompt: str = "You: ", _input=input, _isatty=None):
    r"""Read one logical turn. Blank line submits a multi-line block.

    Behavior:
      - First line typed; if it's empty, returns "" (caller skips the turn).
      - If the first line is a COMMAND or quit (and it's the only line), it is
        returned as-is for single-line command dispatch.
      - Otherwise we keep reading until a BLANK line, assembling a block.
      - Ctrl-C during entry CANCELS the whole block (returns None -> caller
        re-prompts, no partial turn).
      - EOF (Ctrl-D / piped end) submits whatever was gathered, or signals exit
        via EOFError if nothing was gathered on the first line.
      - Non-TTY (piped) input: read everything available as one block; never
        hang waiting for a human blank line.

    Returns the RAW assembled string (caller runs sanitize_input). Returns None
    to mean "cancelled, re-prompt". Raises EOFError to mean "exit".
    """
    import sys
    if _isatty is None:
        try:
            _isatty = sys.stdin.isatty()
        except Exception:
            _isatty = False

    # --- Piped / non-interactive: consume all of stdin as one turn. ---
    if not _isatty:
        data = sys.stdin.read()
        if not data:
            raise EOFError
        return data

    # --- Interactive ---
    try:
        first = _input(prompt)
    except EOFError:
        raise
    # First line empty -> empty turn (caller continues).
    if first.strip() == "":
        return ""
    # Single-line command/quit: return immediately, do NOT enter block mode.
    if looks_like_command(first):
        return first

    lines = [first]
    try:
        while True:
            nxt = _input("")          # continuation prompt is blank
            if nxt.strip() == "":     # blank line submits the block
                break
            lines.append(nxt)
    except EOFError:
        # Ctrl-D: submit what we have (don't lose the block).
        pass
    except KeyboardInterrupt:
        # Ctrl-C: cancel the whole block, no partial turn.
        print("  (input cancelled)")
        return None

    return "\n".join(lines)
