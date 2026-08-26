#!/usr/bin/env python3
"""config.yaml stays pristine; config.local.yaml holds your deltas.
Run: ./.venv/bin/python test_localconfig.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import flags
import localconfig
import yaml

BASE = """\
# Comments here explain trade-offs and must never be rewritten.
model_name: qwen2.5:14b
rga_search_enabled: true
rga_search_allowed_paths:
  - /shipped/example
security_scan_enabled: false        # read-only; no git history
chat_options:
  num_ctx: 8192
  num_predict: 512
caution_controller_enabled: true
"""


def _tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="localcfg_"))
    p = d / "config.yaml"
    p.write_text(BASE, encoding="utf-8")
    return p


def test_no_local_file_means_shipped_defaults():
    base = _tmp()
    cfg = localconfig.load(base)
    assert cfg["model_name"] == "qwen2.5:14b"
    assert cfg["security_scan_enabled"] is False
    assert cfg["rga_search_allowed_paths"] == ["/shipped/example"]
    print("ok: with no local file the shipped defaults apply")


def test_local_wins_and_base_is_never_touched():
    base = _tmp()
    before = base.read_text(encoding="utf-8")
    ok, msg = localconfig.set_key(base, "security_scan_enabled", True)
    assert ok, msg
    assert base.read_text(encoding="utf-8") == before, "config.yaml must not change"
    cfg = localconfig.load(base)
    assert cfg["security_scan_enabled"] is True
    # Untouched keys still fall through, so upgrades still deliver new defaults.
    assert cfg["model_name"] == "qwen2.5:14b"
    print("ok: local wins, config.yaml is byte-identical afterwards")


def test_nested_mapping_merges_one_level():
    base = _tmp()
    localconfig.set_key(base, "chat_options", {"num_ctx": 16384})
    opts = localconfig.load(base)["chat_options"]
    assert opts["num_ctx"] == 16384, opts
    assert opts["num_predict"] == 512, "sibling options must survive"
    print("ok: nested mappings merge instead of clobbering")


def test_list_replaces_rather_than_appends():
    base = _tmp()
    localconfig.set_key(base, "rga_search_allowed_paths", ["/mine/a", "/mine/b"])
    got = localconfig.load(base)["rga_search_allowed_paths"]
    assert got == ["/mine/a", "/mine/b"], got
    assert "/shipped/example" not in got, "lists replace, so a default can be removed"
    print("ok: lists replace, so a shipped default can be dropped")


def test_local_file_is_only_the_delta():
    base = _tmp()
    localconfig.set_key(base, "security_scan_enabled", True)
    data = yaml.safe_load(localconfig.local_path(base).read_text(encoding="utf-8"))
    assert set(data) == {"security_scan_enabled"}, data
    assert "config.local.yaml" in localconfig.local_path(base).name
    text = localconfig.local_path(base).read_text(encoding="utf-8")
    assert text.startswith("# config.local.yaml"), "header must be present"
    assert "Gitignored" in text
    print("ok: the local file contains only the delta, with its header")


def test_unset_restores_the_shipped_default():
    base = _tmp()
    localconfig.set_key(base, "security_scan_enabled", True)
    assert localconfig.load(base)["security_scan_enabled"] is True
    ok, msg = localconfig.unset_key(base, "security_scan_enabled")
    assert ok, msg
    assert localconfig.load(base)["security_scan_enabled"] is False
    bad, _ = localconfig.unset_key(base, "security_scan_enabled")
    assert not bad, "unsetting twice must report, not crash"
    print("ok: unset restores the config.yaml default")


def test_broken_local_file_does_not_break_startup():
    base = _tmp()
    localconfig.local_path(base).write_text("this: is: not: valid: yaml:\n[", encoding="utf-8")
    cfg = localconfig.load(base)
    assert cfg["model_name"] == "qwen2.5:14b", "must fall back to shipped defaults"
    print("ok: an unparseable local file falls back instead of failing")


def test_integrity_guards_still_refused_when_writing_local():
    """Routing writes to the local file must not become a way around the rule."""
    base = _tmp()
    for guarded in ("caution_controller_enabled", "deliberation_enabled",
                    "chain_of_verification_enabled"):
        ok, msg = flags.set_flag_local(base, guarded, False)
        assert not ok, f"{guarded} must not be settable from chat"
        assert "integrity guard" in msg, msg
    assert not localconfig.local_path(base).exists(), "refusal must not create a file"
    print("ok: integrity guards refused on the local path too")


def test_capability_gate_writes_local():
    base = _tmp()
    ok, msg = flags.set_flag_local(base, "security_scan_enabled", True)
    assert ok, msg
    assert "config.local.yaml" in msg, msg
    assert localconfig.load(base)["security_scan_enabled"] is True
    print("ok: a capability gate persists to the local file")


def test_local_keys_reports_overrides():
    base = _tmp()
    assert localconfig.local_keys(base) == []
    localconfig.set_key(base, "security_scan_enabled", True)
    localconfig.set_key(base, "model_name", "llama3.2")
    assert localconfig.local_keys(base) == ["model_name", "security_scan_enabled"]
    print("ok: local_keys lists what you are overriding")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
