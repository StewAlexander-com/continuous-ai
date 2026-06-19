"""Tests for filereader — honest reading of user-attached files (:read).

Covers the honesty-critical behaviors: real contents are returned, truncation is
ALWAYS announced (so the model can't characterize unseen content), binary/missing
files are refused plainly (never guessed), and CSV is summarized structurally.
Pure/deterministic — no model, no network.
"""
import os
import sys
import tempfile

import filereader as fr


def _tmp(content: bytes, suffix=".txt"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content)
    os.close(fd)
    return path


def test_reads_real_text_contents():
    p = _tmp(b"hello\nworld\n", ".txt")
    try:
        ok, block = fr.read_attachment(p)
        assert ok
        assert "hello" in block and "world" in block
        assert "USER-ATTACHED FILE" in block
    finally:
        os.unlink(p)
    print("[PASS] reads real text contents and marks them as user-attached")


def test_python_file_reads():
    p = _tmp(b"def foo():\n    return 42\n", ".py")
    try:
        ok, block = fr.read_attachment(p)
        assert ok and "def foo" in block
    finally:
        os.unlink(p)
    print("[PASS] reads .py files")


def test_truncation_is_announced():
    big = ("\n".join(f"line {i}" for i in range(fr.MAX_TEXT_LINES + 50))).encode()
    p = _tmp(big, ".txt")
    try:
        ok, block = fr.read_attachment(p)
        assert ok
        assert "TRUNCATION NOTICE" in block, "long file must carry a truncation notice"
        assert "do not summarize or claim knowledge of it" in block.lower()
    finally:
        os.unlink(p)
    print("[PASS] truncated text carries an explicit truncation notice")


def test_missing_file_refused_plainly():
    ok, msg = fr.read_attachment("/nonexistent/path/to/file.txt")
    assert not ok and "No file" in msg
    print("[PASS] missing file refused with an honest message (no guessed contents)")


def test_binary_refused():
    p = _tmp(b"\x00\x01\x02\x03BINARY\x00\xff", ".bin")
    try:
        ok, msg = fr.read_attachment(p)
        assert not ok and "binary" in msg.lower()
        assert "won't guess" in msg.lower()
    finally:
        os.unlink(p)
    print("[PASS] binary file refused, contents never guessed")


def test_empty_path_refused():
    ok, msg = fr.read_attachment("")
    assert not ok and "Usage" in msg
    print("[PASS] empty path gives usage, not a crash")


def test_csv_small_shows_all():
    csv_bytes = b"name,age\nAlice,30\nBob,25\n"
    p = _tmp(csv_bytes, ".csv")
    try:
        ok, block = fr.read_attachment(p)
        assert ok
        assert "2 data row" in block and "Alice" in block and "Bob" in block
        assert "name (text)" in block and "age (int)" in block
        # The instructional header mentions the word 'TRUNCATION' generically;
        # what must be ABSENT for a small file is the actual notice marker.
        assert "TRUNCATION NOTICE" not in block, "small CSV should not be truncated"
    finally:
        os.unlink(p)
    print("[PASS] small CSV: full table + inferred column types, no truncation")


def test_csv_large_sampled_with_notice():
    rows = "name,n\n" + "\n".join(f"r{i},{i}" for i in range(fr.CSV_FULL_ROWS + 100))
    p = _tmp(rows.encode(), ".csv")
    try:
        ok, block = fr.read_attachment(p)
        assert ok
        assert "TRUNCATION NOTICE" in block, "large CSV must announce sampling"
        assert f"{fr.CSV_FULL_ROWS + 100} data row" in block
        assert "Do not state totals" in block
    finally:
        os.unlink(p)
    print("[PASS] large CSV: sampled with an explicit 'do not state totals' notice")


def test_oversize_refused():
    big = b"x" * (fr.MAX_BYTES + 1)
    p = _tmp(big, ".txt")
    try:
        ok, msg = fr.read_attachment(p)
        assert not ok and "too large" in msg.lower()
    finally:
        os.unlink(p)
    print("[PASS] oversize file refused before reading")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} filereader checks passed")
    sys.exit(1 if failed else 0)
