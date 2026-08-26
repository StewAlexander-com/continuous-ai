"""filereader — deterministic, honest reading of USER-ATTACHED local files.

This is the runtime half of the `:read` command. The model never reaches files
on its own; the user explicitly names a file, and THIS code (not the model) reads
it and formats real bytes for the prompt. That distinction is what keeps the
no-confabulation guarantee intact: reasoning over text the user genuinely brought
in is not 'pretending to retrieve' — it is a better paste.

Honesty rules baked in:
  * Only paths the user names (with ~ expanded). Glob metacharacters (*, ?, [])
    expand when the literal path does not exist — never autonomous discovery.
  * Refuse binary / undecodable files plainly (never guess contents).
  * On truncation, emit an EXPLICIT in-band notice so the model cannot
    characterize unseen content as if it had read it.
  * CSV is summarized structurally (shape + columns + sample), not dumped raw,
    so a large table neither blows the context window nor invites the model to
    pretend it read every row.
  * PDFs are extracted to page-marked text (PyMuPDF; optional Tesseract OCR on
    scanned pages). Layout/figures may be lossy; truncation is always announced.
  * DOCX (.docx) is extracted via python-docx (paragraphs + tables). Legacy .doc
    is refused with a convert-to-.docx/PDF hint — never guessed.
  * Failed :read paths may offer a numbered pick list only when the named path
    (or glob) is truly absent — never for binary / permission / size / decode
    refusals on a path that exists. Never auto-attached; y / 1-N confirms.
"""
from __future__ import annotations

import csv
import difflib
import errno
import fnmatch
import glob as globmod
import io
import math
import os
import re
from pathlib import Path, PurePosixPath

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
DEFAULT_MAX_DIR_ENTRIES = 10_000  # hard safety cap for directory listings (then page with :more)
DEFAULT_MAX_DIR_PICK = 40         # numbered browse menu size for :read <dir>
DEFAULT_MAX_GLOB_MATCHES = 20   # cap files expanded from a single user glob pattern
DEFAULT_MAX_READ_SUGGESTIONS = 12
DEFAULT_READ_SUGGEST_MIN_SCORE = 0.55

# Absolute-looking paths missing a leading slash (common macOS/Linux paste miss).
# Exact relative paths that exist still win — correction runs only after a miss.
_SOFT_ABS_PREFIXES = (
    "Users/", "Users\\",
    "home/", "home\\",
    "Volumes/", "Volumes\\",
    "mnt/", "mnt\\",
    "private/", "private\\",
)

_GLOB_METACHARS = frozenset("*?[")
_READ_PICK_CANCEL = frozenset({"n", "no", "cancel", ":cancel"})
_READ_PICK_YES = frozenset({"y", "yes"})

# Natural-language read/list requests — conservative; never matches URLs.
_NL_BLOCKED = re.compile(
    r"https?://|www\.|github\.com|gitlab\.com|bitbucket\.org|"
    r"(?:^|\s)(?:read|summarize|open)\s+(?:my\s+)?(?:github|profile|repo)",
    re.I,
)
_NL_HOME_AT = re.compile(
    r"^(?:can you )?(?:please )?read(?: through)?(?: what(?:'s| is)? at)?\s+~/?\??\s*$",
    re.I,
)
_NL_LIST_VERB = re.compile(
    r"^(?:can you )?(?:please )?(?:list|show(?: me)?)"
    r"(?:\s+what(?:'s| is)?(?:\s+in|\s+at)?)?\s+",
    re.I,
)
_NL_READ_VERB = re.compile(
    r"^(?:can you )?(?:please )?(?:read|look at|open|show|cat|type)"
    r"(?:\s+through)?(?:\s+what(?:'s| is)?\s+at)?\s+",
    re.I,
)
_DIR_FILE_FOLLOWUP_VERB = re.compile(
    r"\b(?:review|read|summari[sz]e|analy[sz]e|inspect|audit|explain|open|"
    r"check|look\s+at)\b",
    re.I,
)
_NL_TRAILING_QUESTION = re.compile(
    r"\s+(?:(?:any|what|how|tell me|please)\s+.+|(?:and\s+)?(?:explain|summarize|describe)\s+.+)$",
    re.I,
)


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


def read_suggest_options_from_config(config: dict | None) -> dict:
    c = config or {}
    return {
        "enabled": bool(c.get("read_suggest_enabled", True)),
        "max_candidates": int(c.get("read_suggest_max", DEFAULT_MAX_READ_SUGGESTIONS)),
        "min_score": float(c.get("read_suggest_min_score", DEFAULT_READ_SUGGEST_MIN_SCORE)),
    }


def _is_permission_oserror(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return True
    return False


def _permission_denied_message(path_str: str) -> str:
    name = Path(path_str).name or path_str
    return f"I do not have permission to read this file ({name})."


def _unreadable_content_message(path_str: str) -> str:
    """Binary, opaque extension, or undecodable bytes — never invent contents."""
    name = Path(path_str).name or path_str
    return (f"File appears to be a binary or an extension I cannot read ({name}). "
            "I won't guess its contents.")


def should_offer_read_miss_menu(path_str: str) -> bool:
    """True only when the :read target is absent (literal miss or empty glob).

    Existing paths that fail for binary / permission / size / decode / type must
    print their honest error — never a 'Did you mean' menu that re-offers them.
    """
    raw = (path_str or "").strip()
    if not raw:
        return False
    resolved, _ = resolve_read_path(raw)
    p = Path(os.path.expanduser(resolved))
    try:
        if p.exists():
            return False
    except OSError:
        # Unreadable metadata still means 'something is there' — not a miss menu.
        return False
    return True


def paths_same_target(a: str, b: str) -> bool:
    """Best-effort same-file / same-path compare for suggestion filtering."""
    aa = os.path.expanduser((a or "").strip()).rstrip("/\\")
    bb = os.path.expanduser((b or "").strip()).rstrip("/\\")
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    try:
        if os.path.exists(aa) and os.path.exists(bb) and os.path.samefile(aa, bb):
            return True
    except OSError:
        pass
    try:
        return Path(aa).resolve() == Path(bb).resolve()
    except OSError:
        return False


def drop_existing_pick_candidate(candidates: list[str], target: str) -> list[str]:
    """Remove ``target`` from a pick list when that path still exists on disk.

    Used after a confirmed pick fails to attach (binary / permission / size) so
    the menu cannot re-offer the same dead path. Vanished paths stay listed so
    a true miss can still be recovered if the file reappears under another pick.
    """
    if not candidates:
        return []
    target_exists = False
    try:
        target_exists = Path(os.path.expanduser((target or "").rstrip("/\\"))).exists()
    except OSError:
        target_exists = False
    if not target_exists:
        return list(candidates)
    return [c for c in candidates if not paths_same_target(c, target)]


def _name_match_score(query: str, name: str) -> float:
    """Fuzzy score in [0, 1] for two path stems or short strings."""
    q, n = (query or "").lower(), (name or "").lower()
    if not n:
        return 0.0
    if not q:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.92
    return difflib.SequenceMatcher(None, q, n).ratio()


def _score_path_candidate(query: str, filename: str) -> float:
    """Score a directory filename against the user's attempted basename.

    Uses stem-to-stem fuzzy match so shared extensions (``.py``) do not inflate
    unrelated files. When the query includes an extension, only the same
    extension is considered.
    """
    q_path = Path(query or "")
    n_path = Path(filename or "")
    if not n_path.name:
        return 0.0
    q_suffix = q_path.suffix.lower()
    n_suffix = n_path.suffix.lower()
    if q_suffix and n_suffix and q_suffix != n_suffix:
        return 0.0
    q_stem = q_path.stem if q_path.stem else (query or "")
    n_stem = n_path.stem
    return _name_match_score(q_stem, n_stem)


def _search_directories_for_attempt(attempted: Path) -> list[Path]:
    """Directories we may search — only where the user's path points, never ~ crawl."""
    dirs: list[Path] = []
    parent = attempted.parent
    if parent.exists() and parent.is_dir():
        dirs.append(parent)
        return dirs
    # Parent missing: allow ONE ancestor level (e.g. typo in dirname).
    grand = parent.parent
    if grand.exists() and grand.is_dir() and str(parent) not in ("", ".", "/"):
        dirs.append(grand)
    return dirs


def soft_absolute_form(path_str: str) -> str | None:
    """If path looks like an absolute path missing '/', return the /prefixed form."""
    raw = (path_str or "").strip()
    if not raw or raw.startswith(("/", "~")) or os.path.isabs(raw):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        return None
    for prefix in _SOFT_ABS_PREFIXES:
        if raw.startswith(prefix):
            return "/" + raw.replace("\\", "/")
    return None


def resolve_read_path(path_str: str) -> tuple[str, str | None]:
    """Resolve a user :read path with a non-regressive missing-slash soft fix.

    Returns (path_to_use, notice_or_None).
    Exact existing paths always win. Soft correction only after a miss, and only
    for strong absolute-looking prefixes (Users/..., home/..., etc.).
    """
    raw = (path_str or "").strip()
    if not raw:
        return raw, None
    expanded = os.path.expanduser(raw)
    if Path(expanded).exists():
        return expanded, None
    alt = soft_absolute_form(raw)
    if not alt:
        return expanded, None
    notice = f"Interpreting {raw} as {alt} (added leading /)."
    ap = Path(alt)
    if ap.exists():
        return alt, notice
    if _has_glob_metachars(raw) and expand_read_glob(alt):
        return alt, notice
    # Parent exists → still use corrected form so nearby suggestions work
    # (e.g. Users/.../index → /Users/.../index → suggest index.html).
    if _search_directories_for_attempt(ap):
        return alt, notice
    return expanded, None


def _list_files_in_dir(directory: Path, *, cap: int = 500) -> list[Path]:
    try:
        out: list[Path] = []
        for entry in sorted(directory.iterdir(), key=lambda e: e.name.lower()):
            if len(out) >= cap:
                break
            try:
                if entry.is_file() or entry.is_dir():
                    out.append(entry)
            except OSError:
                continue
        return out
    except OSError:
        return []


def rank_path_candidates(
    path_str: str,
    *,
    max_candidates: int | None = None,
    min_score: float | None = None,
) -> list[str]:
    """Rank real files/dirs near a failed :read path. Pure, deterministic, bounded.

    Only searches directories implied by the user's path (parent, or one ancestor
    if the parent is missing). Never walks the home tree or the filesystem at large.
    Soft-corrects absolute-looking paths missing a leading slash first.
    """
    raw = (path_str or "").strip()
    if not raw:
        return []
    resolved, _ = resolve_read_path(raw)
    cap = max(1, max_candidates or DEFAULT_MAX_READ_SUGGESTIONS)
    floor = min_score if min_score is not None else DEFAULT_READ_SUGGEST_MIN_SCORE
    attempted = Path(os.path.expanduser(resolved))
    query = attempted.name
    use_glob = _has_glob_metachars(query)

    scored: dict[str, float] = {}
    for directory in _search_directories_for_attempt(attempted):
        for entry in _list_files_in_dir(directory):
            if use_glob:
                if not fnmatch.fnmatch(entry.name, query):
                    continue
                score = 1.0
            else:
                score = _score_path_candidate(query, entry.name)
            if score < floor:
                continue
            try:
                full = str(entry.resolve())
            except OSError:
                full = str(entry)
            # Never re-offer the exact attempted path (binary/perm dead-loop defense).
            if paths_same_target(str(attempted), full):
                continue
            if entry.is_dir() and not full.endswith(os.sep):
                full = full + os.sep
            prev = scored.get(full)
            if prev is None or score > prev:
                scored[full] = score

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [path for path, _ in ranked[:cap]]


def format_read_pick_menu(attempted: str, candidates: list[str]) -> str:
    """Human-readable numbered menu for interactive :read disambiguation."""
    lines = [f"No file or directory at {attempted}."]
    kind = "path"
    if len(candidates) == 1:
        lines.append(f"Did you mean this {kind}?")
        lines.append(f"  1  {candidates[0]}")
        lines.append("Reply  y / 1  to attach it,  n  to cancel.")
    else:
        lines.append("Did you mean one of these?")
        for i, path in enumerate(candidates, 1):
            lines.append(f"  {i}  {path}")
        lines.append("Reply with a number (1–{0}), or  n  to cancel.".format(len(candidates)))
    lines.append("Press Return on an empty line to dismiss without attaching.")
    lines.append("Or type a corrected path with  :read <path>  (cancels this menu).")
    return "\n".join(lines)


def parse_read_pick_response(
    text: str,
    candidates: list[str],
    *,
    mode: str = "miss",
    labels: list[str] | None = None,
) -> tuple[str, str | None]:
    """Classify a single-line reply while a read-pick menu is active.

    Returns (action, path):
      ('pick', path)     — user confirmed an existing candidate
      ('cancel', None)   — user declined
      ('review', None)   — empty Return on a directory browse menu
      ('retry', None)    — invalid choice; keep menu open (poka-yoke)
      ('not_pick', None) — not a pick reply; caller should clear menu and continue

    mode='directory': empty input means review the staged listing (not dismiss).
    mode='miss' (default): empty input dismisses the miss-suggest menu.
    """
    if not candidates:
        return "not_pick", None
    t = (text or "").strip()
    if not t:
        if mode == "directory":
            return "review", None
        return "not_pick", None

    low = t.lower()
    if low in _READ_PICK_CANCEL:
        return "cancel", None

    # Exact path the user typed — only if it is one of the offered files.
    expanded = os.path.expanduser(t)
    for cand in candidates:
        cand_cmp = cand.rstrip("/\\")
        if expanded == cand or expanded == os.path.expanduser(cand):
            return "pick", cand
        if expanded.rstrip("/\\") == cand_cmp:
            return "pick", cand
        try:
            if Path(expanded).resolve() == Path(cand_cmp).resolve():
                return "pick", cand
        except OSError:
            pass

    # Unique basename / menu-label match (type index.html instead of the number).
    label_list = labels or []
    name_hits: list[str] = []
    for i, cand in enumerate(candidates):
        base = Path(cand.rstrip("/\\")).name
        lab = label_list[i] if i < len(label_list) else ""
        lab_cmp = lab.rstrip("/\\")
        if low in {base.lower(), (base + "/").lower(), lab.lower(), lab_cmp.lower()}:
            name_hits.append(cand)
    if len(name_hits) == 1:
        return "pick", name_hits[0]
    if len(name_hits) > 1:
        return "retry", None

    if low in _READ_PICK_YES:
        if len(candidates) == 1:
            return "pick", candidates[0]
        # 'y' with multiple choices is ambiguous — keep menu open.
        return "retry", None

    if re.fullmatch(r"\d+", t):
        idx = int(t)
        if 1 <= idx <= len(candidates):
            return "pick", candidates[idx - 1]
        # Out of range — never silently dismiss (poka-yoke).
        return "retry", None

    return "not_pick", None


def list_directory_entries(
    path_str: str,
    *,
    max_entries: int | None = None,
) -> tuple[bool, str, list[tuple[str, str]]]:
    """Return (ok, err_or_dirpath, [(display_name, full_path), ...]).

    Directories get a trailing '/'. Sorted by modification time newest-first,
    then name for deterministic ties. Unknown mtimes sort last. Non-recursive.
    """
    raw = (path_str or "").strip() or "~"
    p = Path(os.path.expanduser(raw))
    if not p.exists():
        return False, f"No directory at {p}", []
    if not p.is_dir():
        return False, f"{p} is not a directory.", []
    cap = max_entries or DEFAULT_MAX_DIR_ENTRIES
    ranked: list[tuple[float | None, str, str]] = []
    try:
        entries = list(p.iterdir())
    except OSError as e:
        return False, f"Cannot list {p}: {e}", []
    for e in entries:
        try:
            if e.is_dir():
                label = e.name + "/"
                full = str(e.resolve()) + os.sep
            else:
                label = e.name
                full = str(e.resolve())
            mtime = _safe_path_mtime(str(e))
            ranked.append((mtime, label, full))
        except OSError:
            ranked.append((None, e.name + " (?)", str(e)))
    # FAT/NTFS may have coarse timestamp resolution, so name is a stable tie-break.
    # None (missing/unusable timestamp) sorts after every real timestamp.
    ranked.sort(key=lambda item: (
        item[0] is None,
        -(item[0] if item[0] is not None else 0.0),
        item[1].casefold(),
    ))
    out = [(label, full) for _mtime, label, full in ranked[:cap]]
    return True, str(p.resolve()), out


def _safe_path_mtime(path_str: str) -> float | None:
    """Portable finite st_mtime, or None when the OS/filesystem cannot supply it."""
    try:
        # Do not strip separators: Path handles them, including Windows drive and
        # UNC roots. st_mtime is modification time on Windows, macOS, and Unix.
        ts = float(Path(os.path.expanduser(path_str)).stat().st_mtime)
    except (OSError, TypeError, ValueError, OverflowError):
        return None
    return ts if math.isfinite(ts) else None


def format_path_modified_time(path_str: str) -> str:
    """Compact local mtime for directory menus; unknown is explicit."""
    from datetime import datetime, timedelta, timezone

    ts = _safe_path_mtime(path_str)
    if ts is None:
        return "unknown modified"
    try:
        return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        # Some Windows C runtimes reject timestamps outside their native range.
        # Arithmetic fallback remains portable for dates representable by datetime.
        try:
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            return (epoch + timedelta(seconds=ts)).astimezone().strftime("%Y-%m-%d %H:%M")
        except (OverflowError, ValueError, OSError):
            return "unknown modified"


def format_directory_browse_menu(
    dir_path: str,
    entries: list[tuple[str, str]],
    *,
    total_count: int | None = None,
) -> str:
    """Numbered menu after :read <dir> — pick a path or Return for the listing."""
    shown = len(entries)
    total = total_count if total_count is not None else shown
    lines = [
        f"Directory: {dir_path}",
        f"  ({shown} shown" + (f" of {total}" if total > shown else "") + ")",
    ]
    if not entries:
        lines.append("  (empty directory)")
    else:
        for i, (label, _full) in enumerate(entries, 1):
            modified = format_path_modified_time(_full)
            lines.append(f"  {i}  {modified}  {label}")
    lines.append("")
    if entries:
        lines.append(f"Reply with a number (1–{shown}) to open that path.")
        lines.append("Or type the exact name (e.g. index.html) when it is unique.")
    lines.append("Press Return to review the full directory listing with Aida.")
    if total > shown:
        lines.append(
            f"(Menu shows the first {shown} entries — use a more specific "
            f":read path for others, or Return for the listing.)"
        )
    lines.append("Out-of-range numbers keep this menu open. n cancels.")
    lines.append("After Return, :more pages a long listing if needed.")
    return "\n".join(lines)


def _suggest(p: Path, *, config: dict | None = None) -> str:
    """Inline passive hint appended to errors when no interactive menu is shown."""
    opts = read_suggest_options_from_config(config)
    if not opts["enabled"]:
        return ""
    try:
        picks = rank_path_candidates(
            str(p),
            max_candidates=min(8, opts["max_candidates"]),
            min_score=opts["min_score"],
        )
        if not picks:
            return ""
        parent = p.parent
        names = [Path(x).name for x in picks]
        return f" Nearby in {parent}: {', '.join(names)}."
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


def is_pdf(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _resolve_pdf_options(pdf_options: dict | None):
    if pdf_options is None:
        from pdfreader import PdfOptions
        return PdfOptions()
    if isinstance(pdf_options, dict):
        from pdfreader import pdf_options_from_config
        return pdf_options_from_config(pdf_options)
    return pdf_options


def _resolve_docx_options(attach_options: dict | None):
    if attach_options is None:
        from docxreader import DocxOptions
        return DocxOptions()
    if isinstance(attach_options, dict):
        from docxreader import docx_options_from_config
        return docx_options_from_config(attach_options)
    return attach_options


def load_file(path_str: str, max_mb: int | None = None,
              pdf_options: dict | None = None) -> tuple[bool, str, str]:
    """Validate + decode a user-named file. Returns (ok, name_or_error, text).

    Does NOT format or truncate -- returns the FULL decoded text so the caller
    can cache it once and page through it with read_chunk(). On failure ok=False,
    the second value is an honest error message, and text is ''.

    ``pdf_options`` is the full app config (or a PdfOptions/DocxOptions-bearing
    dict) used for PDF and DOCX reader settings — name kept for call-site
    compatibility.
    """
    if not path_str or not path_str.strip():
        return False, "No file path given. Usage: :read <path>", ""
    p = Path(os.path.expanduser(path_str.strip()))
    if not p.exists():
        return False, f"No file at {p} -- check the path.{_suggest(p)}", ""
    if not p.is_file():
        return False, f"{p} is not a regular file.", ""
    try:
        size = p.stat().st_size
    except OSError as e:
        if _is_permission_oserror(e):
            return False, _permission_denied_message(str(p)), ""
        return False, f"Cannot stat {p}: {e}", ""
    limit = max_attach_bytes(max_mb)
    if size > limit:
        return False, (f"{p.name} is {size/1024/1024:.1f} MB -- over the {limit//1024//1024} MB "
                       "attach limit. Raise max_attach_mb in config.yaml, or attach an excerpt."), ""
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pdfreader import load_pdf
        return load_pdf(path_str, max_mb, _resolve_pdf_options(pdf_options))
    if suffix == ".docx":
        from docxreader import load_docx
        return load_docx(path_str, max_mb, _resolve_docx_options(pdf_options))
    if suffix == ".doc":
        from docxreader import legacy_doc_refusal
        return False, legacy_doc_refusal(str(p)), ""
    try:
        raw = p.read_bytes()
    except OSError as e:
        if _is_permission_oserror(e):
            return False, _permission_denied_message(str(p)), ""
        return False, f"Cannot read {p}: {e}", ""
    if _looks_binary(raw):
        return False, _unreadable_content_message(str(p)), ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            return False, _unreadable_content_message(str(p)), ""
    return True, p.name, text


def _has_glob_metachars(path_str: str) -> bool:
    return any(c in path_str for c in _GLOB_METACHARS)


def _token_has_path_glob(token: str) -> bool:
    """True when a whitespace token is part of a filesystem glob, not NL punctuation."""
    if "*" in token or "[" in token:
        return True
    if "?" not in token:
        return False
    # Trailing ? on a path-free word (e.g. "insights?") is a question mark, not fnmatch.
    if token.endswith("?") and "/" not in token and "*" not in token:
        return False
    return True


def _strip_path_quotes(path: str) -> str:
    s = (path or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _path_valid_for_read(path_str: str) -> bool:
    """True when a path is readable as-is or as a glob pattern."""
    s = (path_str or "").strip()
    if not s:
        return False
    if _has_glob_metachars(s):
        expanded = os.path.expanduser(s)
        if globmod.glob(expanded):
            return True
        parent = os.path.dirname(expanded) or "."
        return os.path.isdir(parent)
    try:
        return Path(os.path.expanduser(s)).exists()
    except OSError:
        return False


def _split_glob_path_tail(tail: str) -> tuple[str, str | None] | None:
    """When the tail contains glob metacharacters, path ends at the last glob token."""
    tokens = tail.split()
    glob_indices = [i for i, t in enumerate(tokens) if _token_has_path_glob(t)]
    if not glob_indices:
        return None
    glob_idx = glob_indices[-1]
    path = " ".join(tokens[: glob_idx + 1])
    question = " ".join(tokens[glob_idx + 1 :]).strip() or None
    return path, question


def _split_trailing_read_question(tail: str) -> tuple[str, str | None]:
    m = _NL_TRAILING_QUESTION.search(tail)
    if not m:
        return tail, None
    return tail[: m.start()].strip(), m.group(0).strip()


def _looks_like_file_token(token: str) -> bool:
    """Heuristic: first token is plausibly a filename (extension or absolute)."""
    s = (token or "").strip()
    if not s:
        return False
    if s.startswith(("/", "~", "./")):
        return True
    return "." in s and not s.endswith(".")


def _token_names_file_with_suffix(token: str) -> bool:
    """True when a token already names a complete file (real extension).

    Used so a named-but-absent file still splits from a trailing question on a
    machine where that file does not exist. Dotfiles (``~/.zshrc``) are not
    treated as suffixed names.
    """
    s = (token or "").strip()
    if not s:
        return False
    base = s.replace("\\", "/").rsplit("/", 1)[-1]
    if base.startswith(".") and base.count(".") == 1:
        return False
    suffix = PurePosixPath(base).suffix
    return bool(suffix) and 2 <= len(suffix) <= 8 and suffix[1:].isalnum()


def _parse_plain_read_tail(tail: str) -> tuple[str, str | None]:
    """Parse path (+ optional question) after a read/list verb.

    Uses longest-existing-prefix for unquoted paths so spaces in directory names
    work (``read ~/Misc Docs/PDF Documents``). Glob patterns split at the last
    glob token (``.../PDF Documents/*.pdf any insights?``). Trailing natural-
    language questions are peeled before prefix matching when possible.
    """
    import shlex

    tail = (tail or "").strip()
    if not tail:
        return "", None
    if tail[0] in "\"'":
        try:
            parts = shlex.split(tail)
        except ValueError:
            parts = tail.split()
        if not parts:
            return "", None
        path = _strip_path_quotes(parts[0])
        question = " ".join(parts[1:]).strip() or None
        return path, question

    glob_split = _split_glob_path_tail(tail)
    if glob_split:
        return glob_split

    path_part, trailing_q = _split_trailing_read_question(tail)
    tokens = path_part.split()
    for i in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:i])
        if _path_valid_for_read(candidate):
            extra_q = " ".join(tokens[i:]).strip()
            parts = [p for p in (extra_q, trailing_q) if p]
            question = " ".join(parts).strip() or None
            return candidate, question

    # A first token that already names a file (real extension) followed by prose
    # is path + question even when that file is absent from this machine. A
    # later token containing a separator means the space is inside the path, so
    # the whole-tail branch below still owns that case.
    if (len(tokens) >= 2
            and _token_names_file_with_suffix(tokens[0])
            and not any(("/" in t or "\\" in t) for t in tokens[1:])):
        extra_q = " ".join(tokens[1:]).strip()
        parts = [p for p in (extra_q, trailing_q) if p]
        return tokens[0], " ".join(parts).strip() or None

    if len(tokens) >= 2 and tokens[0].startswith(("/", "~")):
        return path_part, trailing_q

    if len(tokens) >= 2 and _looks_like_file_token(tokens[0]):
        extra_q = " ".join(tokens[1:]).strip()
        parts = [p for p in (extra_q, trailing_q) if p]
        question = " ".join(parts).strip() or None
        return tokens[0], question
    return path_part or tail, trailing_q


def parse_read_arg(arg: str) -> tuple[str, str | None]:
    """Parse a ``:read`` argument into (path, optional_question)."""
    return _parse_plain_read_tail(arg)


def detect_local_read_intent(text: str) -> tuple[str, str | None] | None:
    """If the user explicitly asks to read/list a LOCAL path, return (path, question).

    Conservative: single-line only; never matches URLs or GitHub-style requests.
    Used by the REPL to route plain-language read requests to the :read runtime.
    Unquoted paths with spaces are resolved by longest-existing-prefix on disk.
    """
    t = (text or "").strip()
    if not t or "\n" in t:
        return None
    if _NL_BLOCKED.search(t):
        return None
    if _NL_HOME_AT.match(t):
        return "~", None
    m = _NL_LIST_VERB.match(t)
    if m:
        path, _ = _parse_plain_read_tail(t[m.end():])
        return (_strip_path_quotes(path), None) if path else None
    m = _NL_READ_VERB.match(t)
    if m:
        path, question = _parse_plain_read_tail(t[m.end():])
        if not path:
            return None
        return _strip_path_quotes(path), question
    return None


def resolve_directory_file_followup(text: str, directory: str) -> str | None:
    """Resolve one explicitly named direct-child file for a follow-up request.

    This is the safe bridge from a previously user-attached directory listing to
    a file review. It is intentionally conservative:
      * requires an action verb (review/read/summarize/etc.)
      * requires exactly one direct-child filename to appear in the user's text
      * does not recurse or fuzzy-match
      * rejects symlinks (an inferred child must not escape the named directory)

    Returns the real absolute path, or None when ambiguous/not applicable.
    """
    request = (text or "").strip()
    root_raw = (directory or "").strip()
    if not request or "\n" in request or not root_raw:
        return None
    if not _DIR_FILE_FOLLOWUP_VERB.search(request):
        return None
    try:
        root = Path(os.path.expanduser(root_raw)).resolve(strict=True)
        if not root.is_dir():
            return None
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except (OSError, RuntimeError):
        return None

    matches: list[str] = []
    for entry in entries[:DEFAULT_MAX_DIR_ENTRIES]:
        try:
            # Inferred follow-ups never traverse a symlink. Users may still
            # explicitly :read one if they intend to name that target.
            if entry.is_symlink() or not entry.is_file():
                continue
        except OSError:
            continue
        # Filename boundaries prevent index.html matching myindex.html.bak.
        pattern = r"(?<![\w.-])" + re.escape(entry.name) + r"(?![\w.-])"
        if re.search(pattern, request, re.I):
            try:
                matches.append(str(entry.resolve(strict=True)))
            except (OSError, RuntimeError):
                continue
    return matches[0] if len(matches) == 1 else None


def list_directory(path_str: str, *, max_entries: int | None = None) -> tuple[bool, str, str]:
    """List a user-named local directory. Returns (ok, name_or_error, listing_text).

    Builds the full listing (up to a hard safety cap). Callers page large listings
    with read_directory_chunk / :more — they no longer discard entries silently
    after 200.
    """
    raw = (path_str or "").strip()
    if not raw:
        raw = "~"
    p = Path(os.path.expanduser(raw))
    if not p.exists():
        return False, f"No directory at {p} -- check the path.{_suggest(p)}", ""
    if not p.is_dir():
        return False, f"{p} is not a directory.", ""
    cap = max_entries or DEFAULT_MAX_DIR_ENTRIES
    dirs: list[str] = []
    files: list[str] = []
    truncated = False
    try:
        entries = sorted(p.iterdir(), key=lambda e: e.name.lower())
    except OSError as e:
        return False, f"Cannot list {p}: {e}", ""
    for e in entries:
        if len(dirs) + len(files) >= cap:
            truncated = True
            break
        try:
            if e.is_dir():
                dirs.append(e.name + "/")
            elif e.is_file():
                files.append(e.name)
            else:
                files.append(e.name)
        except OSError:
            files.append(e.name + " (?)")
    label = p.name + "/" if p.name else str(p) + "/"
    total_shown = len(dirs) + len(files)
    lines = [f"Directory listing for {p} ({len(dirs)} dir(s), {len(files)} file(s) shown):"]
    if dirs:
        lines.append("\nDirectories:")
        lines.extend(f"  {d}" for d in dirs)
    if files:
        lines.append("\nFiles:")
        lines.extend(f"  {f}" for f in files)
    if not dirs and not files:
        lines.append("(empty directory)")
    body = "\n".join(lines)
    if truncated:
        body += (f"\n\n[TRUNCATION NOTICE: listing capped at {cap} entries "
                 f"({total_shown} shown). Do not claim knowledge of entries "
                 "you were not shown. Type ':more' only pages THIS listing text — "
                 "it cannot reveal names beyond the cap.]")
    return True, f"{label} (directory listing)", body


def expand_read_glob(path_str: str) -> list[Path]:
    """Expand a user-supplied glob pattern to sorted file paths (files only).

    Returns an empty list when the pattern has no glob metacharacters or when
    nothing matches. Never follows symlinks out of band; uses stdlib glob only.
    """
    raw = (path_str or "").strip()
    if not raw or not _has_glob_metachars(raw):
        return []
    expanded = os.path.expanduser(raw)
    matches = sorted(globmod.glob(expanded))
    return [Path(m) for m in matches if os.path.isfile(m)]


def load_glob_files(path_str: str, paths: list[Path], *, max_mb: int | None = None,
                    max_matches: int | None = None,
                    pdf_options: dict | None = None) -> tuple[bool, str, str]:
    """Load multiple files matched by a user glob into one paged text bundle."""
    cap = max_matches or DEFAULT_MAX_GLOB_MATCHES
    truncated = len(paths) > cap
    selected = paths[:cap]
    sections: list[str] = []
    names: list[str] = []
    skipped: list[str] = []
    for p in selected:
        ok, name_or_err, text = load_file(str(p), max_mb, pdf_options=pdf_options)
        if not ok:
            skipped.append(f"{p.name}: {name_or_err}")
            continue
        names.append(name_or_err)
        sections.append(f"=== {name_or_err} ===\n{text}")
    if not sections:
        detail = "; ".join(skipped[:5])
        if len(skipped) > 5:
            detail += f"; ... and {len(skipped) - 5} more"
        return False, (f"No readable text files matched {path_str}"
                       + (f" ({detail})" if detail else "")), ""
    manifest = ", ".join(names)
    body = "\n\n".join(sections)
    if truncated:
        body += (f"\n\n[GLOB TRUNCATION NOTICE: matched {len(paths)} file(s); "
                 f"showing the first {cap}. Do not claim knowledge of files "
                 "you were not shown.]")
    if skipped:
        body += ("\n\n[SKIPPED FILES: " + "; ".join(skipped[:8])
                 + (f"; ... and {len(skipped) - 8} more" if len(skipped) > 8 else "")
                 + "]")
    label = f"{Path(path_str).name or path_str} ({len(names)} file(s))"
    return True, label, body


def load_path(path_str: str, max_mb: int | None = None,
              pdf_options: dict | None = None) -> tuple[bool, str, str]:
    """Load a user-named local file or directory listing.

    Directories return a formatted listing (not recursive). Files delegate to
    load_file(). Glob metacharacters (*, ?, []) expand only when the literal
    path does not exist — a real file named ``foo*bar.txt`` still wins. Soft-
    corrects absolute-looking paths missing a leading slash. Returns
    (ok, name_or_error, text).
    """
    if not path_str or not path_str.strip():
        return False, "No path given. Usage: :read <path>", ""
    raw = path_str.strip()
    resolved, _notice = resolve_read_path(raw)
    p = Path(os.path.expanduser(resolved))
    if p.exists():
        if p.is_dir():
            return list_directory(resolved)
        return load_file(resolved, max_mb, pdf_options=pdf_options)
    if _has_glob_metachars(resolved):
        matches = expand_read_glob(resolved)
        if not matches:
            return False, (f"No files match {resolved} -- check the pattern."
                           f"{_suggest(p)}"), ""
        if len(matches) == 1:
            return load_file(str(matches[0]), max_mb, pdf_options=pdf_options)
        return load_glob_files(resolved, matches, max_mb=max_mb, pdf_options=pdf_options)
    return False, f"No file or directory at {p} -- check the path.{_suggest(p)}", ""


def is_directory_listing(name: str) -> bool:
    return "(directory listing)" in (name or "")


def is_csv(name: str) -> bool:
    return name.lower().endswith(".csv")


def format_csv_block(text: str, name: str) -> str:
    """Full user-attached framing around the CSV structural summary (not paged)."""
    return _wrap(name, _format_csv(text, name))


def format_directory_block(text: str, name: str) -> str:
    """Full user-attached framing around a directory listing (not paged)."""
    return _wrap(name, text)


def read_attachment(path_str: str, max_mb: int | None = None,
                    budget: int | None = None,
                    pdf_options: dict | None = None) -> tuple[bool, str]:
    """Convenience: load + format the FIRST chunk (or full CSV summary) in one
    call. Returns (ok, prompt_block). For paging, callers use load_file +
    read_chunk and track the offset themselves.
    """
    ok, name_or_err, text = load_file(path_str, max_mb, pdf_options=pdf_options)
    if not ok:
        return False, name_or_err
    name = name_or_err
    if is_csv(name):
        return True, format_csv_block(text, name)
    chunk = read_chunk(text, name, char_offset=0, budget=budget or DEFAULT_BUDGET_CHARS)
    return True, chunk["block"]


def read_chunk(text: str, name: str, *, char_offset: int, budget: int,
               kind: str = "file", chunk_no: int | None = None) -> dict:
    """Pure paging: format the slice of `text` from char_offset, up to `budget`
    chars, snapping to a line boundary so a line is never split mid-way (unless a
    single line exceeds the whole budget).

    kind='file' wraps the slice in a code fence; kind='directory' keeps plain
    listing text. Callers paging statefully should pass an explicit 1-based
    chunk_no; offset-derived numbering is only a compatibility fallback.
    Returns {block, next_offset, total, done, chunk_no, shown_chars}.
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
    if chunk_no is None:
        # Line-boundary snapping means offsets are not exact budget multiples.
        # ceil() avoids repeating "chunk 1" on the usual second page; stateful
        # callers pass the exact sequence number and do not depend on this.
        chunk_no = (math.ceil(start / budget) + 1) if start and budget else 1
    else:
        chunk_no = max(1, int(chunk_no))
    label = "listing" if kind == "directory" else "file"
    noun = name

    if kind == "directory":
        body = slice_text
    else:
        body = f"```\n{slice_text}\n```"
    if start == 0 and done:
        notice = ""  # whole attachment fit in one chunk
    else:
        span = f"characters {start:,}-{end:,} of {total:,}"
        if done:
            notice = (f"\n\n[FINAL CHUNK -- {span}. This is the end of {noun}; "
                      f"you have now been shown the {label} across the chunks.]")
        else:
            notice = (f"\n\n[PAGING / TRUNCATION NOTICE -- showing {span} (chunk {chunk_no}). "
                      f"There is MORE of {noun} you have NOT been shown. Do not summarize, "
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


def read_directory_chunk(text: str, name: str, *, char_offset: int, budget: int,
                         chunk_no: int | None = None) -> dict:
    """Page a directory listing (same contract as read_chunk, no code fence)."""
    return read_chunk(
        text, name, char_offset=char_offset, budget=budget,
        kind="directory", chunk_no=chunk_no)


def _wrap(name: str, body: str) -> str:
    """Standard user-attached-file framing around a formatted body.

    Citation contract bounds FILE-content claims only. It must not forbid
    labeled beyond-attachment reasoning (see seedling._read_ask_suffix).
    """
    return (f"[USER-ATTACHED FILE: {name}]\n"
            "The user has explicitly attached this local file; the real contents "
            "are below (read by the runtime, not fetched by you). Reason over them "
            "freely. If a PAGING/TRUNCATION notice appears, do NOT characterize the "
            "unseen portion as if you had read it.\n"
            "Citation contract: when affirming what THIS file says, prefer short "
            "quotes or clear pointers into the shown text. Never invent unread pages. "
            "If the user asks for analysis, options, or pathways beyond the "
            "attachment, you MAY reason and hypothesize using your knowledge — "
            "label clearly ('the text says…' vs 'my reasoning beyond the text…'). "
            "Do not silently put extra claims in the document's mouth. "
            "Do not attribute methods or agendas to a cited author beyond what the "
            "shown text itself says they claimed.\n\n" + body)


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
