#!/usr/bin/env python3
"""Parse and (optionally) interpret a :search tail.

Deterministic first. A model call happens only when the tail looks like
English intent, and it uses a stateless chat_fn — never session.chat,
never deliberation_ledger, never persona/memory.

Scope:
  * file root → that file only
  * depth 1 / 3 / N → rg --max-depth N (1 = direct children)
  * no depth → infinite (today's default)

Kind:
  * content (default)
  * name / both — file and folder names

Quotes:
  * wrapping quotes → exact fixed-string; case-sensitive then insensitive

Unquoted short token without regex metacharacters → exact (-F).
Unquoted with metacharacters → regex.
English intent → interpret into needles, then search, then review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rga_search import looks_like_path, strip_wrapping_quotes

MAX_INTERPRET_PATTERNS = 6
MAX_INTERPRET_PATTERN_LEN = 80
_DEPTH_RE = re.compile(r"\s+depth\s+(all|infinite|inf|[1-9]\d*)\s*$", re.I)
_NAME_PREFIX_RE = re.compile(
    r"^(?P<prefix>"
    r"files?\s+and\s+folders?\s+(?:named\s+)?|"
    r"folders?\s+and\s+files?\s+(?:named\s+)?|"
    r"files?\s+(?:named\s+)?|"
    r"folders?\s+(?:named\s+)?|"
    r"dirs?\s+(?:named\s+)?|"
    r"names?\s+|"
    r"named\s+"
    r")",
    re.I,
)
_REGEX_META_RE = re.compile(r"[.*+?\[\](){}^$|\\]")
_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# Interpreter must not emit "match everything" or the English sentence itself.
_REJECT_INTERPRET_NEEDLES = frozenset({
    ".", ".*", "*", "+", "?", "|", "^", "$", ".+", "\\",
})

INTERPRET_SYS = (
    "You are helping Aida understand a search ask — not compiling a regex. "
    "Turn ANY natural-language ask into short exact literals to look for. "
    "The user may phrase it however they like: a question, a fragment, "
    "'looking for…', 'where do we…', 'retry logic', 'timeouts on connect', etc. "
    "Interpret the meaning, not the wording. Never search for the sentence itself. "
    "JSON only, no markdown: "
    '{"mode":"content"|"name"|"both","depth":null|1|3,'
    '"exact":true,"patterns":["..."],"note":"one short clause"}. '
    "Examples of meaning (not a closed list): "
    "'any loops' → for/while/foreach/do-while; "
    "'retry logic' → retry/backoff/except; "
    "'where is the timeout' → timeout/deadline/sleep; "
    "'files named widget' → mode name, pattern widget. "
    "Max 6 patterns, each under 80 chars. Prefer exact literals. "
    "Do not invent paths. If a file path is already parsed, keep the search "
    "inside that file — do not widen to a directory tree. "
    "If unsure, one conservative pattern and say so in note."
)

FIT_SYS = (
    "Judge whether these search hits answer the user's ask. "
    "You are not compiling a regex. JSON only, no markdown: "
    '{"fit":true|false,"try":null|"short alternative ask"}. '
    "fit=true if the hits are a reasonable answer, even if partial. "
    "fit=false only when the hits are clearly the wrong kind of thing, "
    "or there are zero hits and a different meaning is obvious. "
    "try is one short ask (not a regex, not a path, not the same sentence). "
    "If file_only is true, do not name another path. "
    "If unsure, fit=true and try=null."
)

SMOKE_MAX_HITS = 8
SMOKE_TIMEOUT_S = 4.0
MAX_TRY_ASK_LEN = 120


@dataclass
class SearchSpec:
    original: str
    pattern: str
    patterns: list[str] = field(default_factory=list)
    roots: list[str] | None = None
    quoted: bool = False
    exact: bool = True
    match_kind: str = "content"  # content | name | both
    depth: int | None = None     # None = infinite
    case: str = "default"        # default | sensitive | insensitive | sensitive_then_i
    interpreted: bool = False
    interpret_note: str = ""
    needs_interpret: bool = False
    file_only: bool = False

    def __post_init__(self):
        if not self.patterns:
            self.patterns = [self.pattern] if self.pattern else []

    def summary(self) -> str:
        if self.file_only:
            where = "this file only"
        else:
            where = "depth all" if self.depth is None else f"depth {self.depth}"
        kind = self.match_kind
        how = "exact" if self.exact else "regex"
        case = self.case.replace("_", " ")
        needles = ", ".join(self.patterns[:MAX_INTERPRET_PATTERNS])
        bits = [f"{kind}", where, how, case]
        if self.interpreted:
            bits.append("interpreted")
        line = "; ".join(bits)
        if needles:
            line += f". needles: {needles}"
        if self.interpret_note:
            line += f" ({self.interpret_note})"
        if self.file_only and self.roots:
            line += f"  [{self.roots[0]}]"
        return line


def parse_search_spec(arg: str) -> SearchSpec:
    raw = (arg or "").strip()
    if not raw or raw in ("-h", "--help"):
        return SearchSpec(original=raw, pattern="")
    original = raw
    raw, depth = _take_depth(raw)
    raw, roots = _take_explicit_path(raw)
    # Whole-arg `/path/file.txt` (or a folder) is a scope, not a content needle.
    if not roots and looks_like_path(raw):
        roots = [strip_wrapping_quotes(raw)]
        raw = ""
    file_only = bool(roots) and _is_file_target(roots[0])
    if file_only:
        depth = None
    match_kind, raw = _take_name_prefix(raw)
    if file_only:
        match_kind = "content"
    quoted = _is_quoted(raw)
    pattern = strip_wrapping_quotes(raw)
    exact = True
    case = "default"
    needs_interpret = False
    if not pattern:
        return SearchSpec(
            original=original, pattern="", roots=roots, depth=depth, file_only=file_only,
        )
    if quoted:
        exact = True
        case = "sensitive_then_i"
    elif looks_like_intent(pattern):
        needs_interpret = True
        exact = True
        case = "default"
    elif match_kind != "content":
        exact = False
        case = "insensitive"
    elif _REGEX_META_RE.search(pattern):
        exact = False
        case = "default"
    else:
        exact = True
        case = "default"
    return SearchSpec(
        original=original,
        pattern=pattern,
        patterns=[pattern],
        roots=roots,
        quoted=quoted,
        exact=exact,
        match_kind=match_kind,
        depth=depth,
        case=case,
        needs_interpret=needs_interpret,
        file_only=file_only,
    )


def looks_like_intent(s: str) -> bool:
    """True for any natural-language ask; False for tokens, regex, paths, quotes."""
    t = (s or "").strip()
    if not t or _is_quoted(t):
        return False
    if looks_like_path(t):
        return False
    if _REGEX_META_RE.search(t):
        return False
    if " " in t or "'" in t or "’" in t or "?" in t or "," in t:
        return True
    return not bool(_TOKEN_RE.match(t))


def interpret_search_spec(spec: SearchSpec, *, chat_fn) -> SearchSpec:
    """Fill needles from chat_fn(messages, options=). Never raises."""
    if not spec.needs_interpret or not spec.pattern:
        return spec
    if chat_fn is None:
        spec.interpret_note = "no interpreter; searched the words as typed"
        spec.needs_interpret = False
        return spec
    saved_roots = spec.roots
    saved_file_only = spec.file_only
    user = (
        f"Request: {spec.original}\n"
        f"Already parsed: kind={spec.match_kind} depth={spec.depth!r} "
        f"path={spec.roots!r} file_only={spec.file_only}"
    )
    try:
        raw = chat_fn(
            [
                {"role": "system", "content": INTERPRET_SYS},
                {"role": "user", "content": user},
            ],
            {"num_predict": 220, "temperature": 0.1},
        )
    except Exception as e:
        spec.interpret_note = f"interpretation failed ({e}); searched the words as typed"
        spec.needs_interpret = False
        return spec
    data = _extract_json_object(raw if isinstance(raw, str) else str(raw))
    if not data:
        spec.interpret_note = "interpretation was not JSON; searched the words as typed"
        spec.needs_interpret = False
        return spec
    pats = data.get("patterns") or []
    cleaned: list[str] = []
    for p in pats:
        s = str(p)
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1]
        if not s.strip() or len(s) > MAX_INTERPRET_PATTERN_LEN:
            continue
        if not _keep_interpret_needle(s, spec.pattern):
            continue
        if s not in cleaned:
            cleaned.append(s)
        if len(cleaned) >= MAX_INTERPRET_PATTERNS:
            break
    if not cleaned:
        spec.interpret_note = "interpreter returned no needles; searched the words as typed"
        spec.needs_interpret = False
        return spec
    mode = str(data.get("mode") or spec.match_kind).strip().lower()
    if mode in ("content", "name", "both"):
        spec.match_kind = mode
    depth = data.get("depth", spec.depth)
    if depth in (1, 3) or depth is None:
        spec.depth = depth
    elif isinstance(depth, int) and 1 <= depth <= 20:
        spec.depth = depth
    exact = data.get("exact")
    if isinstance(exact, bool):
        spec.exact = exact
        if not exact:
            spec.case = "default"
    spec.patterns = cleaned
    spec.pattern = cleaned[0]
    spec.interpreted = True
    spec.needs_interpret = False
    spec.interpret_note = str(data.get("note") or "interpreted").strip()[:160]
    spec.roots = saved_roots
    spec.file_only = saved_file_only
    if spec.file_only:
        spec.depth = None
        spec.match_kind = "content"
    return spec


def judge_search_fit(spec: SearchSpec, hits, *, chat_fn) -> tuple[bool, str | None]:
    """Return (fit, try_ask). On doubt or failure, fit=True so we do not nag."""
    if chat_fn is None or not spec.interpreted:
        return True, None
    snippets: list[str] = []
    for h in list(hits or [])[:8]:
        path = getattr(h, "path", "")
        line = getattr(h, "line", 0)
        text = str(getattr(h, "text", ""))[:120]
        snippets.append(f"{path}:{line}: {text}")
    user = (
        f"Ask: {spec.original}\n"
        f"Needles: {spec.patterns}\n"
        f"file_only={spec.file_only} path={spec.roots!r}\n"
        f"Hits ({len(list(hits or []))}):\n"
        + ("\n".join(snippets) if snippets else "(none)")
    )
    try:
        raw = chat_fn(
            [
                {"role": "system", "content": FIT_SYS},
                {"role": "user", "content": user},
            ],
            {"num_predict": 120, "temperature": 0.1},
        )
    except Exception:
        return True, None
    data = _extract_json_object(raw if isinstance(raw, str) else str(raw))
    if not data:
        return True, None
    if data.get("fit") is not False:
        return True, None
    try_ask = str(data.get("try") or "").strip()
    if not try_ask or try_ask.lower() in ("null", "none"):
        return False, None
    if spec_from_try(spec, try_ask) is None:
        return True, None
    return False, try_ask


def spec_from_try(original: SearchSpec, try_ask: str) -> SearchSpec | None:
    """Build a one-shot retry spec. Same roots/file_only. Never widens."""
    t = (try_ask or "").strip()
    if not t or len(t) > MAX_TRY_ASK_LEN:
        return None
    if looks_like_path(t):
        return None
    orig_ask = " ".join((original.original or original.pattern or "").lower().split())
    cand = " ".join(t.lower().split())
    if cand == orig_ask:
        return None
    stripped = orig_ask
    for r in original.roots or []:
        raw = str(r).lower()
        stripped = stripped.replace(" in " + raw, "").replace(raw, "")
    stripped = " ".join(stripped.split())
    if cand == stripped:
        return None
    if not _keep_interpret_needle(t, stripped or original.pattern or ""):
        return None
    spec = parse_search_spec(t)
    if not spec.pattern:
        return None
    spec.roots = original.roots
    spec.file_only = original.file_only
    if spec.file_only:
        spec.depth = None
        spec.match_kind = "content"
    spec.original = t
    return spec


def format_did_you_mean(try_ask: str, *, first_n: int, smoke_n: int) -> str:
    return (
        f"First search ({first_n} hit(s)) does not look like that ask. "
        f"A quicker look for {try_ask!r} found {smoke_n} hit(s)."
    )


def format_search_ask(*, spec: SearchSpec, question: str | None = None) -> str:
    """Citation-grounded review prompt. Hits are shown above this text."""
    how = spec.summary()
    origin = spec.original or spec.pattern
    cite = (
        "Citation contract: every claim ABOUT a match must cite path:line from "
        "the hits above. Do not invent files, lines, or quotes. "
        "If it is not listed, it was not found. "
        "Group by file. If the interpretation might be wrong, say so in one line "
        "and suggest :search \"exact\" or :search name <pat>."
    )
    if question:
        return (
            f"The user searched ({how}). Original: {origin}. "
            f"They now ask: {question} {cite}"
        )
    return (
        f"The user searched ({how}). Original: {origin}. "
        "Review the hits above and say what they actually show. "
        f"{cite} Then await a follow-up."
    )


def format_help_lines() -> list[str]:
    """Shared :help / usage copy — Aida first; flags are optional specificity."""
    return [
        "  :search <what>     Aida interprets the ask, searches, then reviews",
        "                     any English — not a phrase list; may ask if hits miss",
        "                     or be specific: \"quoted\"  name <pat>  <what> /file.txt",
        "                     /path/file.txt after the query = that file only",
        "                     in <dir>  depth 1|3|all  (omit depth = all layers)",
    ]


def _take_depth(raw: str) -> tuple[str, int | None]:
    m = _DEPTH_RE.search(raw)
    if not m:
        return raw.strip(), None
    tok = m.group(1).lower()
    rest = raw[: m.start()].strip()
    if tok in ("all", "infinite", "inf"):
        return rest, None
    return rest, int(tok)


def _take_in_path(raw: str) -> tuple[str, list[str] | None]:
    if " in " not in raw:
        return raw, None
    pat, _, rest = raw.rpartition(" in ")
    rest = rest.strip()
    if looks_like_path(rest):
        # Keep wrapping quotes on the pattern so parse_search_spec can see them.
        return pat.strip(), [strip_wrapping_quotes(rest)]
    return raw, None


def _take_trailing_or_leading_path(raw: str) -> tuple[str, list[str] | None]:
    parts = raw.split()
    if len(parts) < 2:
        return raw, None
    last = strip_wrapping_quotes(parts[-1])
    if looks_like_path(last):
        return " ".join(parts[:-1]), [last]
    first = strip_wrapping_quotes(parts[0])
    if looks_like_path(first):
        return " ".join(parts[1:]), [first]
    return raw, None


def _take_explicit_path(raw: str) -> tuple[str, list[str] | None]:
    rest, roots = _take_in_path(raw)
    if roots:
        return rest, roots
    return _take_trailing_or_leading_path(raw)


def _is_file_target(raw: str) -> bool:
    """True when the named path is a file, or looks like one (.txt, .py, …)."""
    t = strip_wrapping_quotes(raw)
    if not t:
        return False
    try:
        p = Path(t).expanduser()
        if p.is_file():
            return True
        if p.is_dir():
            return False
        return bool(p.suffix)
    except OSError:
        return False


def _take_name_prefix(raw: str) -> tuple[str, str]:
    m = _NAME_PREFIX_RE.match(raw)
    if not m:
        return "content", raw
    rest = raw[m.end():].strip()
    if not rest:
        return "content", raw
    prefix = m.group("prefix").lower()
    if "folder" in prefix or "dir" in prefix:
        if "file" in prefix:
            return "both", rest
        return "name", rest
    if prefix.startswith("name"):
        return "both", rest
    return "name", rest


def _is_quoted(s: str) -> bool:
    t = (s or "").strip()
    return len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'"


def _keep_interpret_needle(needle: str, ask: str) -> bool:
    """Drop match-everything tokens and the English sentence echoed back."""
    t = (needle or "").strip()
    if t in _REJECT_INTERPRET_NEEDLES:
        return False
    a = " ".join((ask or "").lower().split())
    n = " ".join(t.lower().split())
    if not n or not a:
        return True
    if n == a or n.rstrip("?.!") == a.rstrip("?.!"):
        return False
    return True


def _extract_json_object(raw: str) -> dict | None:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
