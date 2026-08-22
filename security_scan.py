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
from rga_search import SearchDenied, permit, run_search

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


def _is_placeholder(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _PLACEHOLDER_MARKERS)


def _classify(text: str) -> str | None:
    if re.search(r"AKIA[0-9A-Z]{16}", text):
        return _KIND_AWS
    if re.search(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY", text) or "-----BEGIN" in text:
        return _KIND_PEM
    if re.search(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b", text):
        return _KIND_IPV4
    if re.search(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b", text):
        return _KIND_IPV6
    return None


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
    use_gitleaks: bool = True,
    use_detect_secrets: bool = True,
    max_hits: int = 200,
) -> tuple[list[SecurityFinding], str]:
    """Return (findings, status_message). Raises SearchDenied on gate failure."""
    if not enabled:
        raise SearchDenied(
            "Security scan is off. Set security_scan_enabled: true in config.yaml "
            "and list rga_search_allowed_paths, then restart."
        )
    ok, err = permit(True, allowed_paths)
    if not ok:
        raise SearchDenied(err.replace("Corpus search", "Security scan"))

    result = run_search(
        _SCAN_PATTERN,
        enabled=True,  # extraction permitted once our gate passed
        allowed_paths=allowed_paths,
        max_hits=max_hits,
        extra_rg_args=["--no-ignore"],  # catch gitignored .env under the allowlist
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
        roots = [str(p) for p in expand_allowed(allowed_paths)]
        for f in _gitleaks_scan(roots):
            findings.append(f)

    if not findings:
        msg = "no matching content found"
        if result.message and result.message != "no matching content found":
            msg = result.message
        return [], msg
    return findings, f"{len(findings)} finding(s)"


def format_scan_report(findings: list[SecurityFinding], message: str) -> str:
    if not findings:
        return message or "no matching content found"
    lines = ["Security scan (read-only; nothing was changed):"]
    for f in findings:
        lines.append(f"  {f.cite()}  [{f.kind}]  {f.excerpt}")
    return "\n".join(lines)
