#!/usr/bin/env python3
"""`:scan <path>` must accept the paths a person actually types.

The reported bug: `:scan stewalexander-com-git/` printed usage, and the usage
then listed /Users/.../stewalexander-com-git as an allowlisted folder.

Run: ./.venv/bin/python test_scan_path.py
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import rga_search as rs
import security_scan as ss


def test_bare_relative_name_is_a_path_not_prose():
    """The reported case, and the shapes around it."""
    for good in ("stewalexander-com-git/", "myfolder/", "a/b", "a/b/c",
                 "~/x", "/x", "./x", "../x", "~/x/"):
        assert ss.looks_like_scan_path(good), f"should be a path: {good!r}"
        roots, err = ss.parse_scan_arg(good)
        assert err == "" and roots == [good], (good, roots, err)
    print("ok: bare relative names with a separator are accepted")


def test_prose_still_rejected():
    """test_security_scan pins this; widening must not break it."""
    for bad in ("please", "help", "-h", "--help", "do the thing",
                "scan my whole computer"):
        roots, err = ss.parse_scan_arg(bad)
        assert err == "usage", f"should be usage: {bad!r} -> {roots, err}"
    roots, err = ss.parse_scan_arg("")
    assert roots is None and err == "", (roots, err)
    print("ok: prose and flags still print usage, empty still means whole allowlist")


def test_bare_existing_folder_name_is_accepted():
    """A single token with no separator, if it really is a folder."""
    d = Path(tempfile.mkdtemp(prefix="scanpath_"))
    try:
        (d / "realdir").mkdir()
        import os
        cwd = os.getcwd()
        os.chdir(d)
        try:
            assert ss.looks_like_scan_path("realdir"), "existing dir should count"
            assert not ss.looks_like_scan_path("definitely-not-here-xyz")
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ok: a bare token that is really a folder counts; a made-up one does not")


def test_candidates_resolve_from_the_allowlist():
    """The user's intent: a bare name matching an allowlisted folder.

    NOTE the folder name here is deliberately unique. An earlier draft used the
    real 'stewalexander-com-git' and failed on the author's own machine, because
    ~/stewalexander-com-git exists and the $HOME fallback correctly matched it
    IN ADDITION to the temp copy — two real candidates, so two results. That was
    the test being unrealistic, not the resolver being wrong, but it is worth
    pinning: a name that matches in two places must never silently collapse.
    """
    d = Path(tempfile.mkdtemp(prefix="scanres_"))
    try:
        target = d / "zz-scanpath-fixture-repo"
        target.mkdir()
        (d / "seedling").mkdir()
        allowed = [str(d / "seedling"), str(target)]
        got = rs.resolve_scan_candidates("zz-scanpath-fixture-repo/", allowed)
        assert got == [target.resolve()], got
        # trailing slash, case, and bare form all land the same place
        for form in ("zz-scanpath-fixture-repo", "ZZ-SCANPATH-FIXTURE-REPO",
                     "zz-scanpath-fixture-repo/"):
            assert rs.resolve_scan_candidates(form, allowed) == [target.resolve()], form
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ok: a bare name resolves to the matching allowlisted folder")


def test_ambiguity_returns_both_and_never_picks():
    d = Path(tempfile.mkdtemp(prefix="scanamb_"))
    try:
        a = d / "one" / "notes"
        b = d / "two" / "notes"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        got = rs.resolve_scan_candidates("notes", [str(a), str(b)])
        assert len(got) == 2, got
        assert set(got) == {a.resolve(), b.resolve()}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ok: two matching folders come back as two, so the caller must ask")


def test_unknown_name_resolves_to_nothing():
    got = rs.resolve_scan_candidates("no-such-folder-anywhere-xyz", ["/tmp"])
    assert got == [], got
    print("ok: an unknown name resolves to nothing rather than a wrong guess")


def test_exact_path_wins_over_allowlist_basename():
    d = Path(tempfile.mkdtemp(prefix="scanwin_"))
    try:
        typed = d / "notes"
        other = d / "elsewhere" / "notes"
        typed.mkdir()
        other.mkdir(parents=True)
        got = rs.resolve_scan_candidates(str(typed), [str(other)])
        assert got[0] == typed.resolve(), got
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ok: what you actually typed is preferred over a name match")


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
