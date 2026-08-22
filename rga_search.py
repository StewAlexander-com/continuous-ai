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
_ZERO_MATCH = "no matching content found"
DEFAULT_MAX_HITS = 50
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MAX_FILESIZE = "4M"


class SearchDenied(Exception):
    """Flag off, empty allowlist, path outside allowlist, or missing binary."""


def rga_binary() -> str | None:
    return shutil.which("rga")


def rg_binary() -> str | None:
    return shutil.which("rg")


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
            "Corpus search is off. Set rga_search_enabled: true in config.yaml "
            "and list rga_search_allowed_paths, then restart."
        )
    if not expand_allowed(allowed_paths):
        return False, (
            "Corpus search has no allowlisted paths. Add directories to "
            "rga_search_allowed_paths in config.yaml."
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


def format_search_block(result: SearchResult) -> str:
    if not result.hits:
        msg = result.message or _ZERO_MATCH
        return (
            f"[USER-DIRECTED SEARCH: {result.query}]\n"
            f"{msg}\n"
            "[end search hits]\n"
        )
    lines = [
        f"[USER-DIRECTED SEARCH: {result.query}]",
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


def _text_argv(pattern: str, roots: list[Path], max_filesize: str) -> list[str] | None:
    rg = rg_binary()
    if not rg:
        return None
    argv = [
        rg, "--json",
        "--max-filesize", max_filesize,
        "--max-count", "20",
    ]
    for g in _EXCLUDE_GLOBS:
        argv.extend(["--glob", g])
    argv.append(pattern)
    argv.extend(str(p) for p in roots)
    return argv


def _doc_argv(pattern: str, roots: list[Path], no_cache: bool) -> list[str] | None:
    rga = rga_binary()
    if not rga:
        return None
    argv = [rga, f"--rga-adapters={_DOC_ADAPTERS}", "--json"]
    if no_cache:
        argv.append("--rga-no-cache")
    for g in _DOC_GLOBS:
        argv.extend(["--glob", g])
    argv.append(pattern)
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
) -> SearchResult:
    """Two-phase search. Raises SearchDenied on gate failure.

    extra_rg_args apply to the text phase only (never a shell string).
    """
    ok, err = permit(enabled, allowed_paths)
    if not ok:
        raise SearchDenied(err)
    pattern = (pattern or "").strip()
    if not pattern:
        raise SearchDenied("Usage: :search <pattern>")

    allowed = expand_allowed(allowed_paths)
    if roots:
        search_roots: list[Path] = []
        for raw in roots:
            p = Path(os.path.expanduser(str(raw).strip())).resolve()
            if not path_is_allowed(p, allowed):
                raise SearchDenied(
                    f"{p} is outside rga_search_allowed_paths. Search stays inside the allowlist."
                )
            search_roots.append(p)
    else:
        search_roots = list(allowed)

    started = time.monotonic()
    budget = float(timeout_s)
    hits: list[SearchHit] = []
    truncated = False
    timed_out = False
    last_err = ""

    text_argv = _text_argv(pattern, search_roots, max_filesize)
    if text_argv:
        if extra_rg_args:
            text_argv[2:2] = list(extra_rg_args)
        h, cap, to, last_err = _stream_json_hits(
            text_argv, allowed=allowed, max_hits=max_hits, timeout_s=budget,
        )
        hits.extend(h)
        truncated = truncated or cap
        timed_out = timed_out or to

    remaining = budget - (time.monotonic() - started)
    if (not truncated) and (not timed_out) and remaining > 0.5 and len(hits) < max_hits:
        doc_argv = _doc_argv(pattern, search_roots, no_cache)
        if doc_argv:
            need = max_hits - len(hits)
            h, cap, to, err2 = _stream_json_hits(
                doc_argv, allowed=allowed, max_hits=need, timeout_s=remaining,
            )
            hits.extend(h)
            truncated = truncated or cap or (len(hits) >= max_hits)
            timed_out = timed_out or to
            last_err = last_err or err2

    if timed_out and hits:
        msg = (
            f"partial: timed out after {timeout_s:.0f}s with {len(hits)} hit(s). "
            "Narrow the path or query for documents still unscanned."
        )
        return SearchResult(query=pattern, hits=hits[:max_hits], truncated=True, message=msg)
    if timed_out and not hits:
        return SearchResult(
            query=pattern,
            hits=[],
            message=f"Search timed out after {timeout_s:.0f}s.",
        )
    if not hits:
        if last_err:
            err_txt = last_err.splitlines()[0][:240]
            # rg exit 1 = no match, no stderr. Real errors usually speak.
            if "No files" in last_err or "error" in last_err.lower():
                return SearchResult(query=pattern, hits=[], message=f"search error: {err_txt}")
        return SearchResult(query=pattern, hits=[], message=_ZERO_MATCH)
    if len(hits) > max_hits:
        hits = hits[:max_hits]
        truncated = True
    return SearchResult(query=pattern, hits=hits, truncated=truncated)
