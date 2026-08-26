#!/usr/bin/env python3
"""Gated secret/IP scanning over allowlisted paths.

Extraction goes through rga_search (subprocess rga), not a second file reader.
detect-secrets is imported as a library when installed (Apache-2.0). gitleaks
is an optional local binary (MIT). No TruffleHog, no git-history scan, no
auto-remediation, no network.

Default-off. Same allowlist as corpus search.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from schemas import SearchHit, SecurityFinding
from rga_search import (
    SearchDenied,
    coerce_max_hits,
    looks_like_path,
    permit,
    run_search,
    strip_wrapping_quotes,
)

# Placeholders that must NOT count as findings (test + common docs).
_PLACEHOLDER_MARKERS = (
    "your_api_key",
    "your-api-key",
    "example",
    "changeme",
    "placeholder",
    "xxx",
    "todo",
    "redacted",
    "insert_key",
    "akidEXAMPLE",
)

# Combined rga pattern: AWS-ish keys, generic PEM headers, IPv4, IPv6 literals.
_SCAN_PATTERN = (
    r"(AKIA[0-9A-Z]{16})"
    r"|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
    r"|-----BEGIN"
    r"|\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
    r"|\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
)

_KIND_AWS = "aws_access_key"
_KIND_PEM = "private_key_block"
_KIND_IPV4 = "ipv4_literal"
_KIND_IPV6 = "ipv6_literal"
_KIND_DETECT_SECRETS = "detect_secrets"
_KIND_GITLEAKS = "gitleaks"
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)
_LOOPBACK_IP = frozenset({"127.0.0.1", "0.0.0.0", "255.255.255.255"})
SCAN_REPORT_CAP = 40
SCAN_HITS_CAP = 400


def _is_placeholder(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _PLACEHOLDER_MARKERS)


def _classify(text: str) -> str | None:
    if re.search(r"AKIA[0-9A-Z]{16}", text):
        return _KIND_AWS
    if re.search(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY", text) or "-----BEGIN" in text:
        return _KIND_PEM
    ips = [ip for ip in _IPV4_RE.findall(text) if ip not in _LOOPBACK_IP]
    if ips:
        return _KIND_IPV4
    if re.search(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b", text):
        return _KIND_IPV6
    return None


def looks_like_scan_path(s: str) -> bool:
    """Argument-position path detector for `:scan`.

    WHY THIS IS NOT rga_search.looks_like_path
    ------------------------------------------
    That predicate is deliberately strict because it has to tell a path from
    ENGLISH inside `:search foo in the logs` — so it accepts only `/`, `~`, `./`
    and `../` prefixes, and nine call sites in search_intent.py depend on exactly
    that. `:scan` has no such ambiguity: its single optional argument is a path
    by construction, there is no prose to disambiguate. Borrowing the strict
    predicate meant `:scan stewalexander-com-git/` was classified as prose and
    answered with usage text that then listed that very folder as allowlisted.

    So: the original prefixes, OR any token containing a separator, OR a single
    bare token that actually exists relative to the CWD or to $HOME. Prose still
    fails: "please" has no separator and is not a folder, which is what
    test_security_scan.test_parse_scan_arg pins.
    """
    from pathlib import Path as _P
    t = strip_wrapping_quotes(s or "").strip()
    if not t:
        return False
    if t.startswith(("/", "~", "./", "../")):
        return True
    if "/" in t or "\\" in t:
        return True
    if " " in t:
        return False
    try:
        if _P(t).expanduser().exists() or (_P.home() / t).exists():
            return True
    except OSError:
        pass
    return False


def parse_scan_arg(arg: str) -> tuple[list[str] | None, str]:
    """Return (roots or None, error). Non-empty error means print usage, do not run.

    None roots = whole allowlist. A path-looking token scopes the scan.
    Other extras are usage — they must not fall through to chat.
    """
    raw = (arg or "").strip()
    if not raw or raw in ("-h", "--help", "help"):
        return None, "usage" if raw else ""
    raw = strip_wrapping_quotes(raw)
    if looks_like_scan_path(raw):
        return [raw], ""
    return None, "usage"


def format_scan_usage(*, enabled: bool, allowed_paths: list[str] | None) -> str:
    from rga_search import expand_allowed
    state = "ON" if enabled else "off"
    paths = expand_allowed(allowed_paths)
    listed = ", ".join(str(p) for p in paths) if paths else "(none — scan denies)"
    return (
        "Usage: :scan\n"
        "       :scan <allowlisted-path>\n"
        f"Scan is {state}. Folders: {listed}\n"
        "Read-only. Nothing is sent to the model. "
        "A path not on the list asks y/N. :allow edits the list."
    )


def _from_hit(hit: SearchHit) -> SecurityFinding | None:
    if _is_placeholder(hit.text):
        return None
    kind = _classify(hit.text)
    if not kind:
        return None
    excerpt = hit.text.strip()
    if len(excerpt) > 160:
        excerpt = excerpt[:157] + "..."
    return SecurityFinding(path=hit.path, line=hit.line, kind=kind, excerpt=excerpt)


def _detect_secrets_on_hits(hits: list[SearchHit]) -> list[SecurityFinding]:
    """Optional library pass over already-extracted line text. No extra IO."""
    try:
        from detect_secrets.plugins.common.util import get_mapping_from_secret_type_to_class
        from detect_secrets.core.potential_secret import PotentialSecret
    except Exception:
        return []
    extra: list[SecurityFinding] = []
    try:
        mapping = get_mapping_from_secret_type_to_class()
        plugins = [cls() for cls in mapping.values()]
    except Exception:
        return []
    for hit in hits:
        if _is_placeholder(hit.text):
            continue
        for plugin in plugins:
            try:
                found = plugin.analyze_line(hit.text, hit.path, line_num=hit.line)
            except Exception:
                continue
            if not found:
                continue
            extra.append(SecurityFinding(
                path=hit.path, line=hit.line, kind=_KIND_DETECT_SECRETS,
                excerpt=hit.text.strip()[:160],
            ))
            break
    return extra


def _gitleaks_binary() -> str | None:
    return shutil.which("gitleaks")


def _gitleaks_scan(roots: list[str], timeout_s: float = 30) -> list[SecurityFinding]:
    """Optional local binary. Never passes --report-format that phones home."""
    bin_path = _gitleaks_binary()
    if not bin_path:
        return []
    findings: list[SecurityFinding] = []
    for root in roots:
        argv = [bin_path, "dir", root, "--no-banner", "--exit-code", "0"]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s, check=False,
                env={**os.environ, "GITLEAKS_CONFIG": "", "NO_COLOR": "1"},
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        # Best-effort parse of default text; tests mock this path.
        for line in (proc.stdout or "").splitlines():
            if _is_placeholder(line):
                continue
            if "Secret:" in line or "leak" in line.lower():
                findings.append(SecurityFinding(
                    path=root, line=0, kind=_KIND_GITLEAKS, excerpt=line.strip()[:160],
                ))
    return findings


def run_scan(
    *,
    enabled: bool,
    allowed_paths: list[str] | None,
    roots: list[str] | None = None,
    use_gitleaks: bool = True,
    use_detect_secrets: bool = True,
    max_hits: int = 200,
) -> tuple[list[SecurityFinding], str]:
    """Return (findings, status_message). Raises SearchDenied on gate failure."""
    if not enabled:
        raise SearchDenied(
            "Security scan is off. Turn it on with  :enable scan  (writes "
            "config.yaml, takes effect immediately), or run  :scan <path>  and "
            "answer y. :capabilities lists flags."
        )
    ok, err = permit(True, allowed_paths)
    if not ok:
        raise SearchDenied(err.replace("Corpus search", "Security scan"))

    max_hits = coerce_max_hits(max_hits, default=200, cap=SCAN_HITS_CAP)
    result = run_search(
        _SCAN_PATTERN,
        enabled=True,  # extraction permitted once our gate passed
        allowed_paths=allowed_paths,
        roots=roots,
        max_hits=max_hits,
        # --hidden is what actually reaches .env. ripgrep skips dotfiles by
        # default, so --no-ignore alone — which only defeats .gitignore — walked
        # straight past the one file this scan exists to check. Verified: an
        # AKIA key in .env is missed without it and found with it, while the
        # same key in a visible file was found either way (which is why every
        # true-positive test used leak.txt and the gap survived).
        #
        # No scan-specific excludes are needed alongside it: rga_search's shared
        # _EXCLUDE_GLOBS already drops .git/, .venv/ and node_modules/ from every
        # text search, so --hidden reaches hidden FILES without walking a repo's
        # object store — which is also what keeps the documented "no git history"
        # scope true. Deliberately no further dotdir excludes beyond those: a
        # secret scanner should err toward noise, never toward a false negative.
        extra_rg_args=["--no-ignore", "--hidden"],
        no_cache=True,
    )
    findings: list[SecurityFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for hit in result.hits:
        f = _from_hit(hit)
        if f is None:
            continue
        key = (f.path, f.line, f.kind)
        if key in seen:
            continue
        seen.add(key)
        findings.append(f)

    if use_detect_secrets:
        for f in _detect_secrets_on_hits(result.hits):
            key = (f.path, f.line, f.kind)
            if key not in seen:
                seen.add(key)
                findings.append(f)

    if use_gitleaks:
        from rga_search import expand_allowed
        gleak_roots = roots if roots else [str(p) for p in expand_allowed(allowed_paths)]
        for f in _gitleaks_scan(gleak_roots):
            findings.append(f)

    if not findings:
        msg = "no matching content found"
        if result.message and result.message != "no matching content found":
            msg = result.message
        return [], msg
    return findings, f"{len(findings)} finding(s)"


def format_scan_report(
    findings: list[SecurityFinding],
    message: str,
    *,
    max_show: int = SCAN_REPORT_CAP,
) -> str:
    if not findings:
        return message or "no matching content found"
    shown = findings[:max_show]
    lines = ["Security scan (read-only; nothing was changed or sent to the model):"]
    for f in shown:
        lines.append(f"  {f.cite()}  [{f.kind}]  {f.excerpt}")
    hidden = len(findings) - len(shown)
    if hidden > 0:
        lines.append(f"  … {hidden} more. Narrow with :scan <path> or shrink the allowlist.")
    return "\n".join(lines)
