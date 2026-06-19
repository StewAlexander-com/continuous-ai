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

# Caps chosen to stay well within a normal context window while being generous.
MAX_BYTES = 256 * 1024          # refuse to even open files larger than 256 KB
MAX_TEXT_LINES = 400            # txt/py: show at most this many lines
MAX_TEXT_CHARS = 24_000         # ...and at most this many chars (whichever first)
CSV_SAMPLE_ROWS = 20            # large CSV: show this many data rows as a sample
CSV_FULL_ROWS = 50             # <= this many rows: show the whole table


def _looks_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    # Heuristic: a high ratio of non-text bytes => binary.
    if not raw:
        return False
    text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
    nontext = sum(b not in text_chars for b in raw[:4096])
    return nontext / min(len(raw), 4096) > 0.30


def read_attachment(path_str: str) -> tuple[bool, str]:
    """Read a user-named file and return (ok, prompt_block).

    prompt_block is ready to be sent as the user turn: it states plainly that the
    USER attached the file, includes the real contents (formatted by type), and
    carries explicit truncation notices where applicable. On failure ok=False and
    the string is an honest error message (no fabricated contents).
    """
    if not path_str or not path_str.strip():
        return False, "No file path given. Usage: :read <path>"
    p = Path(os.path.expanduser(path_str.strip()))

    if not p.exists():
        return False, f"No file at {p} — check the path. (I cannot read files you don't attach.)"
    if not p.is_file():
        return False, f"{p} is not a regular file."
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"Cannot stat {p}: {e}"
    if size > MAX_BYTES:
        return False, (f"{p.name} is {size//1024} KB — too large to attach safely "
                       f"(limit {MAX_BYTES//1024} KB). Attach a smaller file or an excerpt.")

    try:
        raw = p.read_bytes()
    except OSError as e:
        return False, f"Cannot read {p}: {e}"
    if _looks_binary(raw):
        return False, (f"{p.name} looks like a binary file — I only read text "
                       "(.txt, .py, .csv, and similar). I won't guess its contents.")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            return False, f"{p.name} is not decodable as text; I won't guess its contents."

    ext = p.suffix.lower()
    if ext == ".csv":
        body = _format_csv(text, p.name)
    else:
        body = _format_text(text, p.name)

    header = (f"[USER-ATTACHED FILE: {p.name}]\n"
              "The user has explicitly attached this local file; the real contents "
              "are below (read by the runtime, not fetched by you). Reason over them "
              "freely. If a TRUNCATION notice appears, do NOT characterize the "
              "unseen portion as if you had read it.\n\n")
    return True, header + body


def _format_text(text: str, name: str) -> str:
    lines = text.splitlines()
    total = len(lines)
    truncated = False
    if total > MAX_TEXT_LINES:
        lines = lines[:MAX_TEXT_LINES]
        truncated = True
    shown = "\n".join(lines)
    if len(shown) > MAX_TEXT_CHARS:
        shown = shown[:MAX_TEXT_CHARS]
        truncated = True
    block = f"```\n{shown}\n```"
    if truncated:
        block += (f"\n\n[TRUNCATION NOTICE: showing the first part of {name} "
                  f"(~{min(total, MAX_TEXT_LINES)} of {total} lines). The rest was "
                  "not provided — do not summarize or claim knowledge of it.]")
    return block


def _format_csv(text: str, name: str) -> str:
    try:
        reader = list(csv.reader(io.StringIO(text)))
    except Exception:
        # Fall back to treating it as plain text rather than failing.
        return _format_text(text, name)
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
