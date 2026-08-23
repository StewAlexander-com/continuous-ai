#!/usr/bin/env python3
"""Read-only listing of gated capabilities. Nothing here can flip a flag.

Config convention in this repo is top-level `*_enabled` booleans (not a
features.* namespace). This module reads those keys from the live config dict.
"""
from __future__ import annotations

import json
from pathlib import Path

# Flags this expansion owns. Other *_enabled keys are listed too when present
# in config, but only these trigger the one-time startup nudge.
ANNOUNCE = (
    "rga_search_enabled",
    "security_scan_enabled",
)

DESCRIPTIONS = {
    "rga_search_enabled": "Aida search: interprets the ask, searches allowlisted folders, then reviews hits. English is meaning, not a regex. <what> /path/file.txt is that file only.",
    "security_scan_enabled": "Secret/IP scan of allowlisted paths. :scan  or  :scan <path> (read-only; not sent to the model)",
    "live_annotation_enabled": "Inline [REMEMBER] self-annotation (opt-in)",
    "pdf_reader_enabled": "PDF extraction for :read",
    "docx_reader_enabled": "DOCX extraction for :read",
    "deliberation_enabled": "Thesis/antithesis/synthesis on model-derived insights",
    "live_deliberation_enabled": "Background per-turn deliberation",
    "collaborative_wall_enabled": "Ask when a deliberation stalls",
    "caution_controller_enabled": "Forward-acting assertion restraint",
    "chain_of_verification_enabled": "Second-pass honesty rewrite on high caution",
    "osmosis_enabled": "Usage-utility salience polish",
    "reflection_enabled": "Sleep pass (:reflect)",
    "document_osmosis_enabled": "Document-provenance beliefs from :read",
    "background_gate_enabled": "Foreground-priority for background model calls",
    "speak_bias": "Speak lead sentence(s) of long floor-clean replies",
}

ENABLE_PATH = {
    "rga_search_enabled": "Set rga_search_enabled: true in config.yaml, then restart. Folders: :allow or :search … in <path> (asks y/N).",
    "security_scan_enabled": "Set security_scan_enabled: true in config.yaml, then restart. Folders: :allow or :scan <path> (asks y/N).",
}


def _default_seen_path() -> Path:
    return Path(__file__).resolve().parent / "logs" / "seen_features.json"


def enabled_keys(config: dict) -> list[str]:
    """Boolean-ish keys that look like feature flags."""
    keys = []
    for k, v in (config or {}).items():
        if k in DESCRIPTIONS or k.endswith("_enabled") or k in ANNOUNCE:
            if isinstance(v, bool):
                keys.append(k)
    # Stable order: announced first, then the rest alphabetically.
    announced = [k for k in ANNOUNCE if k in keys]
    rest = sorted(k for k in keys if k not in ANNOUNCE)
    return announced + rest


def format_listing(config: dict) -> str:
    """Human listing. Read-only; never writes config."""
    lines = ["Gated capabilities (read-only — this command cannot turn anything on):"]
    for key in enabled_keys(config):
        on = bool(config.get(key))
        state = "ON" if on else "off"
        desc = DESCRIPTIONS.get(key, "")
        how = ENABLE_PATH.get(key, f"Set {key} in config.yaml, then restart.")
        extra = f"  {desc}" if desc else ""
        lines.append(f"  {key}: {state}.{extra}")
        if not on and key in ENABLE_PATH:
            lines.append(f"      enable: {how}")
    paths = config.get("rga_search_allowed_paths") or []
    lines.append(f"  rga_search_allowed_paths: {paths if paths else '[] (search/scan deny all)'}")
    return "\n".join(lines)


def load_seen(path: Path | None = None) -> set[str]:
    p = path or _default_seen_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("seen") or [])
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


def save_seen(seen: set[str], path: Path | None = None) -> None:
    p = path or _default_seen_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"seen": sorted(seen)}, indent=2) + "\n", encoding="utf-8")


def nudge_lines(config: dict, *, seen_path: Path | None = None) -> list[str]:
    """One line per newly-seen announced flag. Marks them seen. Cannot mutate flags."""
    seen = load_seen(seen_path)
    lines: list[str] = []
    new_seen = set(seen)
    for key in ANNOUNCE:
        if key in seen:
            continue
        on = bool((config or {}).get(key))
        desc = DESCRIPTIONS.get(key, key)
        state = "ON" if on else "off"
        lines.append(f"New capability: {key} is {state}. {desc}")
        new_seen.add(key)
    if new_seen != seen:
        save_seen(new_seen, seen_path)
    return lines
