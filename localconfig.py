#!/usr/bin/env python3
"""seedling/localconfig.py — personal settings that never touch the repo.

WHY THIS EXISTS
---------------
`config.yaml` has to ship: it carries the defaults and the comments that explain
every trade-off, and Aida does not run without it. But it is also the file a user
actually changes — `:enable scan`, `:allow <path>`, a different `model_name` —
and every one of those edits landed in a tracked file. The result was a working
tree permanently dirty with settings that are true for exactly one machine, and a
`git pull` that could conflict on them.

So: `config.yaml` is the shipped default and is read-only in practice.
`config.local.yaml` is gitignored, holds only your deltas, and wins. Every
runtime write (`:enable`, `:disable`, `:allow`, `:allow drop`) goes to the local
file, so using Aida can no longer dirty the repository.

MERGE RULES — deliberately boring, so the effective config is predictable
------------------------------------------------------------------------
  * A top-level key present in the local file WINS outright.
  * A nested mapping is merged one level deep, so `chat_options: {num_ctx: 16384}`
    overrides that one option without dropping the rest.
  * A LIST REPLACES the base list; it is never appended to. Union semantics would
    make it impossible to remove a shipped default, and silent accumulation is
    exactly the kind of surprise this project avoids. Callers that mean "add one"
    therefore write the whole effective list — which is what the `:allow` path
    does.

The local file is written with a plain YAML dumper rather than the line-surgical
editing `flags.set_flag_yaml` uses on `config.yaml`. That is on purpose: the
tracked file is mostly comments worth preserving, whereas this one is a short
machine-specific delta, so a clean round-trip is safer than clever editing. The
header below is rewritten on every save.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LOCAL_NAME = "config.local.yaml"

HEADER = """\
# config.local.yaml — YOUR settings. Gitignored; never committed.
#
# Only put deltas here. Anything absent falls through to config.yaml, so you get
# new defaults on upgrade instead of a stale copy of the whole file.
#
# Written automatically by :enable / :disable / :allow / :theme. Safe to edit by hand.
# Lists replace the shipped list rather than adding to it.
"""


def local_path(base_path: Path | str) -> Path:
    """The local override that sits beside a given config.yaml."""
    return Path(base_path).parent / LOCAL_NAME


def _read(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        # A broken local file must not take the assistant down; the shipped
        # defaults are still perfectly usable.
        return {}


def merge(base: dict, over: dict) -> dict:
    """Overlay `over` onto `base` by the rules in the module docstring."""
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def load(base_path: Path | str) -> dict:
    """Effective config: shipped defaults with your local deltas on top."""
    base_path = Path(base_path)
    base = _read(base_path)
    return merge(base, _read(local_path(base_path)))


def local_keys(base_path: Path | str) -> list[str]:
    """Which keys you are currently overriding. Used by :capabilities."""
    return sorted(_read(local_path(base_path)).keys())


def set_key(base_path: Path | str, key: str, value) -> tuple[bool, str]:
    """Persist one key to the local override. Returns (ok, message).

    Never writes `base_path`. A failure here is not fatal to the caller: the
    live config dict has already been updated, so the setting holds for this
    session and only the persistence was lost — which is what the callers report
    as "session only".
    """
    p = local_path(base_path)
    data = _read(p)
    data[key] = value
    try:
        body = yaml.safe_dump(data, default_flow_style=False, sort_keys=True,
                              allow_unicode=True)
        p.write_text(HEADER + "\n" + body, encoding="utf-8")
    except OSError as e:
        return False, f"could not write {p.name}: {e}"
    return True, f"saved {key} to {p.name} (your settings; not committed)"


def unset_key(base_path: Path | str, key: str) -> tuple[bool, str]:
    """Drop a local override so the shipped default applies again."""
    p = local_path(base_path)
    data = _read(p)
    if key not in data:
        return False, f"{key} is not overridden in {p.name}"
    del data[key]
    try:
        if data:
            body = yaml.safe_dump(data, default_flow_style=False, sort_keys=True,
                                  allow_unicode=True)
            p.write_text(HEADER + "\n" + body, encoding="utf-8")
        else:
            p.write_text(HEADER, encoding="utf-8")
    except OSError as e:
        return False, f"could not write {p.name}: {e}"
    return True, f"removed {key} from {p.name}; the config.yaml default applies"
