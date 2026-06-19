"""filereader — deterministic, honest reading of USER-ATTACHED local files.

This is the runtime half of the `:read` command. The model never reaches files
on its own; the user explicitly names a file, and THIS code (not the model) reads
it and formats real bytes for the prompt. That distinction is what keeps the
no-confabulation guarantee intact: reasoning over text the user genuinely brought
in is not 'pretending to retrieve' — it is a better paste.

Honesty rules baked in:
  * Only the exact path the user names (with ~ expanded). No autonomous discovery.
  * Refuse binary / undecodable files plainly (never guess contents).
  * On truncation, emit an EXPLICIT in-band notice so the model cannot
    characterize unseen content as if it had read it.
  * CSV is summarized structurally (shape + columns + sample), not dumped raw,
    so a large table neither blows the context window nor invites the model to
    pretend it read every row.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

# --- File ACCEPTANCE limit (can we open it at all?) ---
# This is separate from how much we SHOW the model. We accept large files (up to
# ~50 MB by default, configurable) and page through them with :more; we never
# pour a whole large file into the context window (physically impossible).
DEFAULT_MAX_ATTACH_MB = 50

# --- Display heuristics ---
# Rough chars-per-token for budgeting without a tokenizer dependency.
CHARS_PER_TOKEN = 4
# Fallback per-chunk budget when no context size is known (Ollama's default
# num_ctx is small, so keep a conservative floor that still shows something useful).
DEFAULT_BUDGET_CHARS = 8_000
MIN_BUDGET_CHARS = 2_000
CSV_SAMPLE_ROWS = 20            # large CSV: show this many data rows as a sample
CSV_FULL_ROWS = 50             # <= this many rows: show the whole table


def budget_chars(num_ctx: int | None) -> int:
    """Per-chunk character budget derived from the model's context window.

    Reserve part of num_ctx for the system prompt + history + the reply, and
    convert the rest to a char budget. Scales with the user's actual num_ctx
    (bump it in config for bigger chunks). Honest floor so we always show
    *something* even on a tiny default context.
    """
    if not num_ctx or num_ctx <= 0:
        return DEFAULT_BUDGET_CHARS
    reserve_tokens = 1500  # system prompt + recent history + room for the reply
    usable = max(0, num_ctx - reserve_tokens)
    return max(MIN_BUDGET_CHARS, usable * CHARS_PER_TOKEN)


def max_attach_bytes(max_mb: int | None = None) -> int:
    return int((max_mb or DEFAULT_MAX_ATTACH_MB) * 1024 * 1024)


def _suggest(p: Path) -> str:
    """Best-effort 'did you mean' hint: list a few existing entries in the parent
    directory (preferring names similar to the attempted one). Exception-safe and
    bounded so it never crashes the read or floods a huge directory.
    """
    try:
        parent = p.parent
        if not parent.exists() or not parent.is_dir():
            return ""
        stem = p.stem.lower()
        entries = [e.name for e in parent.iterdir() if e.is_file()]
        if not entries:
            return ""
        # Prefer names sharing the stem; else just the first few alphabetically.
        similar = [n for n in entries if stem and stem[:3] in n.lower()]
        picks = (similar or sorted(entries))[:8]
        if not picks:
            return ""
        return f" Nearby in {parent}: {', '.join(picks)}."
    except Exception:
        return ""


def _looks_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    # Heuristic: a high ratio of non-text bytes => binary.
    if not raw:
        return False
    text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
    nontext = sum(b not in text_chars for b in raw[:4096])
    return nontext / min(len(raw), 4096) > 0.30


def load_file(path_str: str, max_mb: int | None = None) -> tuple[bool, str, str]:
    """Validate + decode a user-named file. Returns (ok, name_or_error, text).

    Does NOT format or truncate -- returns the FULL decoded text so the caller
    can cache it once and page through it with read_chunk(). On failure ok=False,
    the second value is an honest error message, and text is ''.
    """
    if not path_str or not path_str.strip():
        return False, "No file path given. Usage: :read <path>", ""
    p = Path(os.path.expanduser(path_str.strip()))
    if not p.exists():
        return False, f"No file at {p} -- check the path.{_suggest(p)} (I cannot read files you don't attach.)", ""
    if not p.is_file():
        return False, f"{p} is not a regular file.", ""
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"Cannot stat {p}: {e}", ""
    limit = max_attach_bytes(max_mb)
    if size > limit:
        return False, (f"{p.name} is {size/1024/1024:.1f} MB -- over the {limit//1024//1024} MB "
                       "attach limit. Raise max_attach_mb in config.yaml, or attach an excerpt."), ""
    try:
        raw = p.read_bytes()
    except OSError as e:
        return False, f"Cannot read {p}: {e}", ""
    if _looks_binary(raw):
        return False, (f"{p.name} looks like a binary file -- I only read text "
                       "(.txt, .py, .csv, and similar). I won't guess its contents."), ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            return False, f"{p.name} is not decodable as text; I won't guess its contents.", ""
    return True, p.name, text


def is_csv(name: str) -> bool:
    return name.lower().endswith(".csv")


def format_csv_block(text: str, name: str) -> str:
    """Full user-attached framing around the CSV structural summary (not paged)."""
    return _wrap(name, _format_csv(text, name))


def read_attachment(path_str: str, max_mb: int | None = None,
                    budget: int | None = None) -> tuple[bool, str]:
    """Convenience: load + format the FIRST chunk (or full CSV summary) in one
    call. Returns (ok, prompt_block). For paging, callers use load_file +
    read_chunk and track the offset themselves.
    """
    ok, name_or_err, text = load_file(path_str, max_mb)
    if not ok:
        return False, name_or_err
    name = name_or_err
    if is_csv(name):
        return True, format_csv_block(text, name)
    chunk = read_chunk(text, name, char_offset=0, budget=budget or DEFAULT_BUDGET_CHARS)
    return True, chunk["block"]


def read_chunk(text: str, name: str, *, char_offset: int, budget: int) -> dict:
    """Pure paging: format the slice of `text` from char_offset, up to `budget`
    chars, snapping to a line boundary so a line is never split mid-way (unless a
    single line exceeds the whole budget).

    Returns {block, next_offset, total, done, chunk_no, shown_chars}. Every
    partial view carries an explicit, honest paging/truncation notice so the
    model can never characterize the unseen remainder.
    """
    budget = max(MIN_BUDGET_CHARS, int(budget))
    total = len(text)
    start = max(0, min(char_offset, total))
    end = min(total, start + budget)
    if end < total:
        nl = text.rfind("\n", start, end)
        if nl > start:
            end = nl + 1
    slice_text = text[start:end]
    done = end >= total
    chunk_no = (start // budget) + 1 if budget else 1

    body = f"```\n{slice_text}\n```"
    if start == 0 and done:
        notice = ""  # whole file fit in one chunk
    else:
        span = f"characters {start:,}-{end:,} of {total:,}"
        if done:
            notice = (f"\n\n[FINAL CHUNK -- {span}. This is the end of {name}; "
                      "you have now been shown the file across the chunks.]")
        else:
            notice = (f"\n\n[PAGING / TRUNCATION NOTICE -- showing {span} (chunk {chunk_no}). "
                      f"There is MORE of {name} you have NOT been shown. Do not summarize, "
                      "total, or claim knowledge of the unseen portion. The user can type "
                      "':more' to reveal the next part.]")
    return {
        "block": _wrap(name, body + notice),
        "next_offset": end,
        "total": total,
        "done": done,
        "chunk_no": chunk_no,
        "shown_chars": end - start,
    }


def _wrap(name: str, body: str) -> str:
    """Standard user-attached-file framing around a formatted body."""
    return (f"[USER-ATTACHED FILE: {name}]\n"
            "The user has explicitly attached this local file; the real contents "
            "are below (read by the runtime, not fetched by you). Reason over them "
            "freely. If a PAGING/TRUNCATION notice appears, do NOT characterize the "
            "unseen portion as if you had read it.\n\n" + body)

def _format_csv(text: str, name: str) -> str:
    try:
        reader = list(csv.reader(io.StringIO(text)))
    except Exception:
        # Malformed CSV: fall back to a capped raw view rather than failing.
        snippet = text[:DEFAULT_BUDGET_CHARS]
        more = "" if len(text) <= DEFAULT_BUDGET_CHARS else (
            "\n\n[NOTE: CSV could not be parsed; showing a raw excerpt only — "
            "do not assume structure or totals.]")
        return f"(could not parse {name} as CSV; raw excerpt)\n```\n{snippet}\n```" + more
    if not reader:
        return f"{name} is an empty CSV (no rows)."

    header = reader[0]
    data = reader[1:]
    ncols, nrows = len(header), len(data)

    # Infer a coarse column type from the first non-empty value in each column.
    def coltype(idx):
        for row in data:
            if idx < len(row) and row[idx].strip():
                v = row[idx].strip()
                try:
                    int(v); return "int"
                except ValueError:
                    pass
                try:
                    float(v); return "float"
                except ValueError:
                    return "text"
        return "empty"

    types = [coltype(i) for i in range(ncols)]
    col_desc = ", ".join(f"{h} ({t})" for h, t in zip(header, types))

    show_all = nrows <= CSV_FULL_ROWS
    sample = data if show_all else data[:CSV_SAMPLE_ROWS]

    def render(rows):
        out = [" | ".join(header), " | ".join("---" for _ in header)]
        for r in rows:
            out.append(" | ".join((r[i] if i < len(r) else "") for i in range(ncols)))
        return "\n".join(out)

    block = (f"CSV summary for {name}: {nrows} data row(s), {ncols} column(s).\n"
             f"Columns: {col_desc}\n\n" + render(sample))
    if not show_all:
        block += (f"\n\n[TRUNCATION NOTICE: showing the first {len(sample)} of "
                  f"{nrows} rows. Do not state totals, aggregates, or claims about "
                  "rows you were not shown unless they can be computed from the sample.]")
    return block
