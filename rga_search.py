#!/usr/bin/env python3
"""Gated corpus search via `rg` (fast text) then `rga` (PDF/Office).

Subprocess only — never imports rga internals. Default-off at the caller:
enabled=True AND a non-empty allowlist.

Speed contract (RCA: Desktop 30s timeout dropped stdout):
  * Stream JSONL; stop at max_hits; keep partial hits on timeout.
  * Phase 1: ripgrep on text/code with --max-filesize and media/archive globs.
  * Phase 2: rga poppler+pandoc only (no zip/tar/decompress) if budget remains.
  * Archive adapters are off for :search so a Downloads/Desktop zip bomb
    cannot stall the query.

Do not edit filereader.py / pdfreader.py / docxreader.py to do this work.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from schemas import SearchHit, SearchResult

_DOC_ADAPTERS = "poppler,pandoc"
_EXCLUDE_GLOBS = (
    "!**/*.epub",
    "!**/*.fb2",
    "!**/*.mkv",
    "!**/*.mp4",
    "!**/*.mp3",
    "!**/*.mov",
    "!**/*.MOV",
    "!**/*.avi",
    "!**/*.webm",
    "!**/*.flac",
    "!**/*.wav",
    "!**/*.sqlite",
    "!**/*.sqlite3",
    "!**/*.db",
    "!**/*.zip",
    "!**/*.tar",
    "!**/*.gz",
    "!**/*.tgz",
    "!**/*.bz2",
    "!**/*.xz",
    "!**/*.7z",
    "!**/*.rar",
    "!**/*.dmg",
    "!**/*.pkg",
    "!**/*.iso",
    "!**/*.exe",
    "!**/*.app",
    "!**/*.pyc",
    "!**/*.onnx",
    "!**/*.bin",
    "!**/.git/**",
    "!**/.venv/**",
    "!**/node_modules/**",
)
_DOC_GLOBS = ("*.pdf", "*.docx", "*.odt", "*.pptx", "*.xlsx")
_SKIP_DIR_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".tox"}
_ZERO_MATCH = "no matching content found"
DEFAULT_MAX_HITS = 50
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MAX_FILESIZE = "4M"
MAX_HITS_CAP = 200
MAX_TIMEOUT_S = 60.0
MIN_TIMEOUT_S = 1.0
MAX_PATTERN_LEN = 400
_FILESIZE_RE = re.compile(r"^\d+[KMG]?$")


class SearchDenied(Exception):
    """Flag off, empty allowlist, path outside allowlist, or missing binary."""


class PathNotAllowlisted(SearchDenied):
    """Named root exists but is outside the allowlist. REPL may offer to add it."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(
            f"{path} is outside rga_search_allowed_paths. "
            "Allow it when asked, or add it with :allow / config.yaml."
        )


def rga_binary() -> str | None:
    return shutil.which("rga")


def rg_binary() -> str | None:
    return shutil.which("rg")


def strip_wrapping_quotes(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def looks_like_path(s: str) -> bool:
    """True for explicit paths only — not English ('in the logs')."""
    t = strip_wrapping_quotes(s)
    if not t:
        return False
    return t.startswith(("/", "~", "./", "../"))


def parse_search_arg(arg: str) -> tuple[str, list[str] | None]:
    """Parse `:search` tail into (pattern, optional roots).

    Empty pattern means print usage. `in <path>` only wins when the suffix
    looks like a path, so `:search something in the logs` stays a pattern.
    """
    from search_intent import parse_search_spec
    spec = parse_search_spec(arg)
    return spec.pattern, spec.roots


def coerce_max_hits(n, default: int = DEFAULT_MAX_HITS, cap: int = MAX_HITS_CAP) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def coerce_timeout_s(t, default: float = DEFAULT_TIMEOUT_S) -> float:
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = default
    return max(MIN_TIMEOUT_S, min(t, MAX_TIMEOUT_S))


def coerce_max_filesize(s, default: str = DEFAULT_MAX_FILESIZE) -> str:
    s = str(s or "").strip()
    return s if _FILESIZE_RE.fullmatch(s) else default


def format_search_usage(*, enabled: bool, allowed_paths: list[str] | None) -> str:
    from search_intent import format_help_lines
    state = "ON" if enabled else "off"
    paths = expand_allowed(allowed_paths)
    listed = ", ".join(str(p) for p in paths) if paths else "(none — search denies)"
    body = "\n".join(line.strip() for line in format_help_lines())
    return (
        f"{body}\n"
        f"Search is {state}. Folders: {listed}\n"
        "A path not on the list asks y/N. :allow lists / adds / drops."
    )


def format_allow_listing(allowed_paths: list[str] | None) -> str:
    paths = expand_allowed(allowed_paths)
    lines = ["Search/scan allowlist (:allow <path>  /  :allow drop N):"]
    if not paths:
        lines.append("  (empty — named paths will ask to add)")
    else:
        for i, p in enumerate(paths, 1):
            lines.append(f"  {i}. {p}")
    return "\n".join(lines)


def parse_allow_arg(arg: str) -> tuple[str, str]:
    """Return (action, rest) where action is list|add|drop|usage."""
    raw = strip_wrapping_quotes(arg or "")
    if not raw or raw in ("-h", "--help", "list"):
        return "list", ""
    low = raw.lower()
    if low.startswith("drop ") or low.startswith("rm ") or low.startswith("- "):
        return "drop", raw.split(None, 1)[1].strip()
    if looks_like_path(raw):
        return "add", raw
    return "usage", raw


def resolve_named_root(raw: str) -> Path:
    return Path(os.path.expanduser(strip_wrapping_quotes(str(raw)))).resolve()


def _flow_empty(rest: str) -> bool:
    return rest.strip() in ("", "[]", "~")


def add_allowed_path_yaml(config_path: Path, new_raw: str) -> tuple[bool, str]:
    """Append one allowlist entry. Preserves comments. Does not enable flags."""
    try:
        resolved = resolve_named_root(new_raw)
    except OSError as e:
        return False, f"cannot resolve path: {e}"
    if not (resolved.is_dir() or resolved.is_file()):
        return False, f"{resolved} does not exist."
    if not config_path.is_file():
        return False, f"{config_path} not found."
    text = config_path.read_text(encoding="utf-8")
    current = _paths_listed_in_yaml(text)
    allowed = expand_allowed(current)
    if path_is_allowed(resolved, allowed):
        return False, f"{resolved} is already covered by the allowlist."
    new_line = f"  - {resolved}"
    updated = _insert_allow_item(text, new_line)
    if updated is None:
        return False, "could not find rga_search_allowed_paths in config.yaml."
    config_path.write_text(updated, encoding="utf-8")
    return True, f"added {resolved} to rga_search_allowed_paths"


def drop_allowed_path_yaml(config_path: Path, which: str) -> tuple[bool, str]:
    """Remove one allowlist entry by 1-based index or path. Preserves comments."""
    if not config_path.is_file():
        return False, f"{config_path} not found."
    text = config_path.read_text(encoding="utf-8")
    current = _paths_listed_in_yaml(text)
    allowed = expand_allowed(current)
    target: Path | None = None
    token = strip_wrapping_quotes(which)
    if token.isdigit():
        idx = int(token)
        if 1 <= idx <= len(allowed):
            target = allowed[idx - 1]
    if target is None:
        try:
            cand = resolve_named_root(token)
        except OSError:
            cand = None
        if cand is not None:
            for p in allowed:
                if p == cand:
                    target = p
                    break
    if target is None:
        return False, f"no allowlist entry matches {which!r}."
    updated = _remove_allow_item(text, target)
    if updated is None:
        return False, "could not edit rga_search_allowed_paths in config.yaml."
    config_path.write_text(updated, encoding="utf-8")
    return True, f"removed {target} from rga_search_allowed_paths"


def apply_allowed_path_to_config(config: dict, resolved: Path) -> None:
    lst = [str(p) for p in (config.get("rga_search_allowed_paths") or []) if str(p).strip()]
    if str(resolved) not in lst:
        lst.append(str(resolved))
    config["rga_search_allowed_paths"] = lst


def apply_drop_to_config(config: dict, resolved: Path) -> None:
    kept = []
    for raw in config.get("rga_search_allowed_paths") or []:
        try:
            if resolve_named_root(raw) == resolved:
                continue
        except OSError:
            pass
        kept.append(raw)
    config["rga_search_allowed_paths"] = kept


def _paths_listed_in_yaml(text: str) -> list[str]:
    try:
        data = __import__("yaml").safe_load(text) or {}
    except Exception:
        return []
    return [str(p) for p in (data.get("rga_search_allowed_paths") or [])]


def _insert_allow_item(text: str, new_line: str) -> str | None:
    lines = text.splitlines(keepends=True)
    key_re = re.compile(r"^(rga_search_allowed_paths\s*:\s*)(.*)$")
    item_re = re.compile(r"^(\s+)-\s+\S")
    key_i = None
    for i, line in enumerate(lines):
        if key_re.match(line.rstrip("\n")):
            key_i = i
            break
    if key_i is None:
        return None
    m = key_re.match(lines[key_i].rstrip("\n"))
    rest = (m.group(2) if m else "").strip()
    value, comment = rest, ""
    if " #" in rest:
        value, comment = rest.split(" #", 1)
        value, comment = value.strip(), " #" + comment
    elif rest.startswith("#"):
        value, comment = "", " " + rest
    last_item = key_i
    found_item = False
    for j in range(key_i + 1, len(lines)):
        if item_re.match(lines[j]):
            last_item = j
            found_item = True
            continue
        break
    if found_item:
        lines.insert(last_item + 1, new_line + "\n")
        return "".join(lines)
    if value and not _flow_empty(value):
        return None
    lines[key_i] = f"rga_search_allowed_paths:{comment}\n"
    lines.insert(key_i + 1, new_line + "\n")
    return "".join(lines)


def _remove_allow_item(text: str, target: Path) -> str | None:
    lines = text.splitlines(keepends=True)
    key_re = re.compile(r"^rga_search_allowed_paths\s*:")
    item_re = re.compile(r"^(\s+)-\s+(.+?)\s*$")
    key_i = None
    for i, line in enumerate(lines):
        if key_re.match(line):
            key_i = i
            break
    if key_i is None:
        return None
    item_indices = []
    for j in range(key_i + 1, len(lines)):
        m = item_re.match(lines[j].rstrip("\n"))
        if m:
            item_indices.append(j)
            continue
        if lines[j].strip() == "" or lines[j].lstrip().startswith("#"):
            continue
        break
    drop_i = None
    for j in item_indices:
        raw = item_re.match(lines[j].rstrip("\n")).group(2)
        raw = strip_wrapping_quotes(raw)
        try:
            if resolve_named_root(raw) == target:
                drop_i = j
                break
        except OSError:
            if raw == str(target):
                drop_i = j
                break
    if drop_i is None:
        return None
    del lines[drop_i]
    remaining = [k for k in item_indices if k != drop_i]
    if not remaining:
        # Keep a valid empty list so YAML stays a list, not null.
        lines[key_i] = "rga_search_allowed_paths: []\n"
    return "".join(lines)


def expand_allowed(allowed_paths: list[str] | None) -> list[Path]:
    out: list[Path] = []
    for raw in allowed_paths or []:
        if not str(raw).strip():
            continue
        out.append(Path(os.path.expanduser(str(raw).strip())).resolve())
    return out


def path_is_allowed(path: Path, allowed: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in allowed:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def permit(enabled: bool, allowed_paths: list[str] | None) -> tuple[bool, str]:
    if not enabled:
        return False, (
            "Corpus search is off. Turn it on with  :enable search  (writes "
            "config.yaml, takes effect immediately), or run  :search … in <path>  "
            "and answer y. :capabilities lists flags."
        )
    if not expand_allowed(allowed_paths):
        return False, (
            "Corpus search has no allowlisted paths. Add directories to "
            "rga_search_allowed_paths in config.yaml. :capabilities lists flags."
        )
    if not (rga_binary() or rg_binary()):
        return False, (
            "Neither rg nor rga is installed. Install ripgrep (and optionally "
            "ripgrep-all) yourself — the runtime will not vendor them."
        )
    return True, ""


def _recover_line(path: str, snippet: str) -> int:
    if not snippet:
        return 0
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    needle = snippet.rstrip("\n")
    for i, ln in enumerate(lines, 1):
        if needle in ln:
            return i
    return 0


def _parse_rg_json_line(raw: str) -> SearchHit | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "match":
        return None
    data = obj.get("data") or {}
    path = ((data.get("path") or {}).get("text")) or ""
    text = ((data.get("lines") or {}).get("text")) or ""
    if not path:
        return None
    line = data.get("line_number")
    if line is None:
        line = _recover_line(path, text)
    return SearchHit(path=path, line=int(line), text=text.rstrip("\n"))


def format_search_block(result: SearchResult, *, preamble: str = "") -> str:
    if not result.hits:
        msg = result.message or _ZERO_MATCH
        head = f"[USER-DIRECTED SEARCH: {result.query}]\n"
        if preamble:
            head += preamble.rstrip() + "\n"
        return (
            f"{head}"
            f"{msg}\n"
            "[end search hits]\n"
        )
    lines = [
        f"[USER-DIRECTED SEARCH: {result.query}]",
    ]
    if preamble:
        lines.append(preamble.rstrip())
    lines += [
        "Citation contract: every claim ABOUT a match must cite path:line from "
        "the hits below. Do not invent files, lines, or quotes. "
        "If it is not listed, it was not found.",
        "",
    ]
    for h in result.hits:
        snippet = h.text.replace("\t", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        lines.append(f"{h.cite()}: {snippet}")
    if result.truncated:
        extra = result.message or (
            "truncated: more matches exist; raise rga_search_max_hits or narrow the query"
        )
        lines.append(f"[{extra}]")
    elif result.message:
        lines.append(f"[{result.message}]")
    lines.append("[end search hits]")
    return "\n".join(lines) + "\n"


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass


def _stream_json_hits(
    argv: list[str],
    *,
    allowed: list[Path],
    max_hits: int,
    timeout_s: float,
) -> tuple[list[SearchHit], bool, bool, str]:
    """Run argv, parse rg/rga JSONL as it arrives. Keep hits if we have to kill."""
    if timeout_s <= 0 or max_hits <= 0:
        return [], False, True, ""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    lines_q: queue.Queue[str | None] = queue.Queue()

    def _read() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines_q.put(line)
        finally:
            lines_q.put(None)

    threading.Thread(target=_read, daemon=True).start()
    hits: list[SearchHit] = []
    truncated = False
    timed_out = False
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = lines_q.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if proc.poll() is not None and lines_q.empty():
                    break
                continue
            if line is None:
                break
            hit = _parse_rg_json_line(line)
            if hit is None:
                continue
            if not path_is_allowed(Path(hit.path), allowed):
                continue
            hits.append(hit)
            if len(hits) >= max_hits:
                truncated = True
                break
    finally:
        if proc.poll() is None:
            _stop_proc(proc)
    err = ""
    if proc.stderr is not None:
        try:
            err = (proc.stderr.read() or "").strip()
        except Exception:
            err = ""
    return hits, truncated, timed_out, err


def _text_argv(
    pattern: str,
    roots: list[Path],
    max_filesize: str,
    *,
    exact: bool = False,
    case: str = "default",
    max_depth: int | None = None,
) -> list[str] | None:
    rg = rg_binary()
    if not rg:
        return None
    argv = [
        rg, "--json",
        "--max-filesize", max_filesize,
        "--max-count", "20",
    ]
    if max_depth is not None:
        argv.extend(["--max-depth", str(max_depth)])
    if exact:
        argv.append("-F")
    if case == "sensitive":
        argv.append("-s")
    elif case == "insensitive":
        argv.append("-i")
    for g in _EXCLUDE_GLOBS:
        argv.extend(["--glob", g])
    argv.extend(["--", pattern])
    argv.extend(str(p) for p in roots)
    return argv


def _doc_argv(
    pattern: str,
    roots: list[Path],
    no_cache: bool,
    *,
    exact: bool = False,
    case: str = "default",
) -> list[str] | None:
    rga = rga_binary()
    if not rga:
        return None
    argv = [rga, f"--rga-adapters={_DOC_ADAPTERS}", "--json"]
    if no_cache:
        argv.append("--rga-no-cache")
    if exact:
        argv.append("-F")
    if case == "sensitive":
        argv.append("-s")
    elif case == "insensitive":
        argv.append("-i")
    for g in _DOC_GLOBS:
        argv.extend(["--glob", g])
    argv.extend(["--", pattern])
    argv.extend(str(p) for p in roots)
    return argv


def run_search(
    pattern: str,
    *,
    enabled: bool,
    allowed_paths: list[str] | None,
    roots: list[str] | None = None,
    max_hits: int = DEFAULT_MAX_HITS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    extra_rg_args: list[str] | None = None,
    no_cache: bool = False,
    max_filesize: str = DEFAULT_MAX_FILESIZE,
    exact: bool = False,
    case: str = "default",
    max_depth: int | None = None,
    match_kind: str = "content",
    extra_patterns: list[str] | None = None,
) -> SearchResult:
    """Two-phase search. Raises SearchDenied on gate failure.

    extra_rg_args apply to the text phase only (never a shell string).
    """
    ok, err = permit(enabled, allowed_paths)
    if not ok:
        raise SearchDenied(err)
    needles: list[str] = []
    for raw_n in [pattern, *(extra_patterns or [])]:
        t = str(raw_n or "")
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        if t.strip() and t not in needles:
            needles.append(t)
    if not needles:
        raise SearchDenied("Usage: :search <pattern>")
    for n in needles:
        if len(n) > MAX_PATTERN_LEN:
            raise SearchDenied(
                f"Pattern is {len(n)} characters; keep it under {MAX_PATTERN_LEN}."
            )

    max_hits = coerce_max_hits(max_hits)
    timeout_s = coerce_timeout_s(timeout_s)
    max_filesize = coerce_max_filesize(max_filesize)
    query = needles[0] if len(needles) == 1 else pattern or needles[0]

    allowed = expand_allowed(allowed_paths)
    if roots:
        search_roots: list[Path] = []
        for raw in roots:
            p = resolve_named_root(raw)
            if not (p.is_dir() or p.is_file()):
                raise SearchDenied(f"{p} does not exist.")
            if not path_is_allowed(p, allowed):
                raise PathNotAllowlisted(str(p))
            search_roots.append(p)
    else:
        search_roots = list(allowed)

    present: list[Path] = []
    missing: list[Path] = []
    for p in search_roots:
        if p.is_dir() or p.is_file():
            present.append(p)
        else:
            missing.append(p)
    if not present:
        shown = ", ".join(str(p) for p in missing) or "(none)"
        raise SearchDenied(
            f"None of the search paths exist: {shown}. "
            "Fix rga_search_allowed_paths in config.yaml."
        )
    search_roots = present
    missing_note = (
        f"skipped missing path(s): {', '.join(str(p) for p in missing)}"
        if missing else ""
    )

    started = time.monotonic()
    budget = float(timeout_s)
    hits: list[SearchHit] = []
    truncated = False
    timed_out = False
    last_err = ""
    want_names = match_kind in ("name", "both")
    want_content = match_kind in ("content", "both")
    if case == "sensitive_then_i":
        passes = ["sensitive", "insensitive"]
    else:
        passes = [case]

    for needle in needles:
        if len(hits) >= max_hits:
            truncated = True
            break
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            break
        for case_pass in passes:
            if len(hits) >= max_hits:
                truncated = True
                break
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            if want_names:
                h = _name_hits(
                    needle, search_roots, allowed,
                    max_hits=max_hits - len(hits),
                    max_depth=max_depth,
                    exact=exact,
                    insensitive=(case_pass == "insensitive"),
                )
                hits.extend(h)
                if len(hits) >= max_hits:
                    truncated = True
                    break
            if not want_content:
                continue
            h, cap, to, err = _content_hits(
                needle, search_roots, allowed,
                max_hits=max_hits - len(hits),
                timeout_s=remaining,
                max_filesize=max_filesize,
                extra_rg_args=extra_rg_args,
                no_cache=no_cache,
                exact=exact,
                case=case_pass,
                max_depth=max_depth,
            )
            hits.extend(h)
            truncated = truncated or cap
            timed_out = timed_out or to
            last_err = last_err or err

    hits = _dedup_hits(hits)
    if len(hits) > max_hits:
        hits = hits[:max_hits]
        truncated = True

    if timed_out and hits:
        msg = (
            f"partial: timed out after {timeout_s:.0f}s with {len(hits)} hit(s). "
            "Narrow the path or query for documents still unscanned."
        )
        return _finish(query, hits, truncated=True, message=msg, extra=missing_note)
    if timed_out and not hits:
        return _finish(
            query, [], message=f"Search timed out after {timeout_s:.0f}s.", extra=missing_note,
        )
    if not hits:
        if last_err:
            err_txt = last_err.splitlines()[0][:240]
            low = last_err.lower()
            if "regex" in low or "No files" in last_err or "error" in low:
                return _finish(
                    query, [], message=f"search error: {err_txt}", extra=missing_note,
                )
        return _finish(query, [], message=_ZERO_MATCH, extra=missing_note)
    return _finish(query, hits, truncated=truncated, extra=missing_note)


def _content_hits(
    pattern: str,
    search_roots: list[Path],
    allowed: list[Path],
    *,
    max_hits: int,
    timeout_s: float,
    max_filesize: str,
    extra_rg_args: list[str] | None,
    no_cache: bool,
    exact: bool,
    case: str,
    max_depth: int | None,
) -> tuple[list[SearchHit], bool, bool, str]:
    started = time.monotonic()
    hits: list[SearchHit] = []
    truncated = False
    timed_out = False
    last_err = ""
    text_argv = _text_argv(
        pattern, search_roots, max_filesize,
        exact=exact, case=case, max_depth=max_depth,
    )
    if text_argv:
        if extra_rg_args:
            text_argv[2:2] = list(extra_rg_args)
        h, cap, to, last_err = _stream_json_hits(
            text_argv, allowed=allowed, max_hits=max_hits, timeout_s=timeout_s,
        )
        hits.extend(h)
        truncated = truncated or cap
        timed_out = timed_out or to
    remaining = timeout_s - (time.monotonic() - started)
    if (not truncated) and (not timed_out) and remaining > 0.5 and len(hits) < max_hits:
        doc_argv = _doc_argv(
            pattern, search_roots, no_cache, exact=exact, case=case,
        )
        if doc_argv:
            h, cap, to, err2 = _stream_json_hits(
                doc_argv, allowed=allowed, max_hits=max_hits - len(hits), timeout_s=remaining,
            )
            hits.extend(h)
            truncated = truncated or cap or (len(hits) >= max_hits)
            timed_out = timed_out or to
            last_err = last_err or err2
    return hits, truncated, timed_out, last_err


def _name_hits(
    pattern: str,
    search_roots: list[Path],
    allowed: list[Path],
    *,
    max_hits: int,
    max_depth: int | None,
    exact: bool,
    insensitive: bool,
) -> list[SearchHit]:
    hits: list[SearchHit] = []
    needle = pattern.lower() if insensitive else pattern
    for root in search_roots:
        for path, kind in _iter_names(root, max_depth):
            if not path_is_allowed(path, allowed):
                continue
            name = path.name
            hay = name.lower() if insensitive else name
            if exact:
                ok = hay == needle
            else:
                ok = needle in hay
            if not ok:
                continue
            hits.append(SearchHit(path=str(path), line=0, text=f"[{kind}] {name}"))
            if len(hits) >= max_hits:
                return hits
    return hits


def _iter_names(root: Path, max_depth: int | None):
    """Yield (path, 'file'|'folder') at rg-compatible depths (1 = direct children)."""
    if root.is_file():
        yield root, "file"
        return
    if not root.is_dir():
        return
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
            here = Path(dirpath)
            try:
                depth_here = 0 if here.resolve() == root.resolve() else len(here.resolve().relative_to(root.resolve()).parts)
            except ValueError:
                dirnames[:] = []
                continue
            child_depth = depth_here + 1
            if max_depth is not None and child_depth > max_depth:
                dirnames[:] = []
                continue
            for d in list(dirnames):
                yield here / d, "folder"
            for f in filenames:
                if f.startswith("."):
                    continue
                yield here / f, "file"
            if max_depth is not None and child_depth >= max_depth:
                dirnames[:] = []
    except OSError:
        return


def _dedup_hits(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[tuple[str, int]] = set()
    out: list[SearchHit] = []
    for h in hits:
        key = (h.path, h.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _finish(
    query: str,
    hits: list[SearchHit],
    *,
    truncated: bool = False,
    message: str = "",
    extra: str = "",
) -> SearchResult:
    if extra:
        message = f"{message} ({extra})" if message else extra
    return SearchResult(query=query, hits=hits, truncated=truncated, message=message)
