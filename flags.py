#!/usr/bin/env python3
"""seedling/flags.py — the ONLY write path for boolean capability flags.

WHY THIS EXISTS
---------------
`capabilities.py` is deliberately read-only ("this command cannot turn anything
on"), and that property is worth keeping: introspection should never be able to
change behaviour. But the consequence was a dead end. A user who typed

    :scan ~/some-project/

got told to open config.yaml, set a boolean, and restart the program — while the
machinery to add that same folder to the allowlist inline, with a y/N prompt and
a comment-preserving YAML write, already existed three lines away in
`rga_search.add_allowed_path_yaml`. This module is the boolean equivalent, so the
gate can be opened the same way the allowlist already is: by asking.

WHAT MAY BE FLIPPED FROM CHAT, AND WHAT MAY NOT
-----------------------------------------------
`CHAT_TOGGLEABLE` is an explicit allowlist, NOT "every key ending in _enabled".
It holds capability gates — flags that decide whether Aida is permitted to read
more of your disk. Those are the user's call, they are read-only operations, and
the friction of a restart buys nothing.

Everything else stays YAML-only ON PURPOSE. `caution_controller_enabled`,
`deliberation_enabled`, `chain_of_verification_enabled` and friends are integrity
guards: they are the reason confabulation measures ~0% instead of ~20%. A chat
command that could switch them off would let a single sentence disable the
project's central claim, and worse, would let the model reach them by suggestion.
Widening a capability gate is a permission grant. Narrowing an honesty guard is a
regression, and it should cost you a file edit and a restart.

NO RESTART IS REQUIRED, AND THE OLD MESSAGE WAS WRONG ABOUT THAT
----------------------------------------------------------------
Both gates are read from the live config dict at the moment the command runs
(`seedling._handle_scan_command`, `seedling._handle_search_command`), not
captured into session state at startup. So `apply_flag_to_config` takes effect on
the very next command. The YAML write is for the NEXT run; the dict write is for
this one. Either can fail independently, and a failed file write degrades to
"session only" rather than refusing — the same shape as the allowlist path.
"""
from __future__ import annotations

import re
from pathlib import Path

# Capability gates the user may open or close conversationally. See module
# docstring for why this is a short explicit list and not a pattern match.
CHAT_TOGGLEABLE: frozenset[str] = frozenset({
    "rga_search_enabled",
    "security_scan_enabled",
})

# Shown when someone tries to toggle a flag that is not conversationally
# toggleable, so the refusal explains itself instead of just saying no.
GUARDED_REASON = (
    "that flag is an integrity guard, not a capability gate — it stays in "
    "config.yaml on purpose, so honesty behaviour cannot be changed by a "
    "sentence in chat"
)


def is_toggleable(key: str) -> bool:
    return key in CHAT_TOGGLEABLE


def normalize_flag_name(raw: str) -> str:
    """Accept 'scan', 'security_scan', or the full key. Never guesses across
    flags: an unknown token is returned as-is so the caller can refuse it."""
    token = (raw or "").strip().strip(":").replace("-", "_").lower()
    if not token:
        return ""
    if token in CHAT_TOGGLEABLE:
        return token
    if not token.endswith("_enabled"):
        candidate = f"{token}_enabled"
        if candidate in CHAT_TOGGLEABLE:
            return candidate
    aliases = {
        "scan": "security_scan_enabled",
        "security": "security_scan_enabled",
        "search": "rga_search_enabled",
        "rga": "rga_search_enabled",
    }
    return aliases.get(token, token)


def apply_flag_to_config(config: dict, key: str, value: bool) -> None:
    """In-memory set. Takes effect on the next command in this session."""
    config[key] = bool(value)


def set_flag_yaml(config_path: Path, key: str, value: bool) -> tuple[bool, str]:
    """Flip one top-level boolean in config.yaml, preserving everything else.

    Line-surgical on purpose: round-tripping the file through a YAML dumper
    would silently delete every comment in it, and this config is mostly
    comments explaining the trade-offs. Any trailing inline comment on the
    edited line is kept (`security_scan_enabled: false  # read-only; ...`).
    """
    if not is_toggleable(key):
        return False, f"{key} is not toggleable from chat — {GUARDED_REASON}."
    if not config_path.is_file():
        return False, f"{config_path} not found."
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"could not read {config_path}: {e}"

    want = "true" if value else "false"
    # Top-level key only: no leading whitespace, so a same-named key nested
    # under some other mapping is never touched.
    pattern = re.compile(
        rf"^(?P<key>{re.escape(key)}\s*:\s*)(?P<val>\S+)(?P<rest>.*)$"
    )
    lines = text.splitlines(keepends=True)
    hit = None
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            hit = (i, m)
            break
    if hit is None:
        return False, f"could not find a top-level {key} in {config_path.name}."

    i, m = hit
    current = m.group("val").strip().lower()
    if current == want:
        return False, f"{key} is already {want} in {config_path.name}."

    newline = "\n" if lines[i].endswith("\n") else ""
    lines[i] = f"{m.group('key')}{want}{m.group('rest').rstrip()}{newline}"
    try:
        config_path.write_text("".join(lines), encoding="utf-8")
    except OSError as e:
        return False, f"could not write {config_path}: {e}"
    return True, f"set {key}: {want} in {config_path.name}"


def read_flag_yaml(config_path: Path, key: str) -> bool | None:
    """What the FILE says, independent of the live dict. Used to tell
    'on for this session' apart from 'on for good'."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(
        rf"^{re.escape(key)}\s*:\s*(\S+)", text, re.MULTILINE
    )
    if not m:
        return None
    return m.group(1).strip().lower() in ("true", "yes", "on", "1")
