"""Core vs versioned regression patches for guard text (non-regressive split)."""
import sys

sys.path.insert(0, ".")
import guards as G
import session as S

_p = 0
_f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}")


def test_session_reexports_assembled_guard():
    check("session._GUARD_TEXT is assembled GUARD_TEXT", S._GUARD_TEXT == G.GUARD_TEXT)
    check("version pinned", G.REGRESSION_PATCHES_VERSION == "1.0.0")
    check("session exports version", S.REGRESSION_PATCHES_VERSION == "1.0.0")
    print("[PASS] session re-exports assembled guard")


def test_case_specific_live_in_patches_not_core():
    core = G._GUARD_CORE_TEXT.lower()
    patches = G._REGRESSION_PATCHES_TEXT.lower()
    full = G.GUARD_TEXT.lower()

    # Case-specific: Declaration clause enumeration
    check("doi clauses not in core", "lives, fortunes, and sacred honor" not in core)
    check("doi clauses in patches", "lives, fortunes, and sacred honor" in patches)
    check("doi clauses in full", "lives, fortunes, and sacred honor" in full)

    # Case-specific: exact eval-forbid marker strings
    check("retrieval marker not in core", "[retrieval complete]" not in core)
    check("retrieval marker in patches", "[retrieval complete]" in patches
          or "retrieval complete" in patches)
    check("retrieval marker in full", "retrieval complete" in full)

    # 5-pass style script
    check("5-pass script not in core", "memorizing a script" not in core)
    check("5-pass script in patches", "memorizing a script" in patches)

    # Core still has the principles
    check("core has user-invoked process", "user-invoked process" in core)
    check("core has never claim retrieved", "never claim to have retrieved" in core)
    check("patches header versioned", "regression patches (v1.0.0" in patches)
    print("[PASS] case-specific text lives in versioned patches")


def test_patch_entry_ids_stable():
    ids = [e["id"] for e in G._REGRESSION_PATCH_ENTRIES]
    check("expected patch ids present", set(ids) >= {
        "process-5pass-style",
        "process-doi-conclusion-clauses",
        "forbid-retrieval-marker-strings",
    })
    check("ids unique", len(ids) == len(set(ids)))
    print("[PASS] patch entry ids stable")


if __name__ == "__main__":
    test_session_reexports_assembled_guard()
    test_case_specific_live_in_patches_not_core()
    test_patch_entry_ids_stable()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
