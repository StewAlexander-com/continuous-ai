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
    # A file larger than the default budget, read via read_attachment (chunk 1),
    # must carry an explicit paging notice in its BODY (not just the header).
    big = ("\n".join(f"line {i}" for i in range(20000))).encode()
    p = _tmp(big, ".txt")
    try:
        ok, block = fr.read_attachment(p)   # uses DEFAULT_BUDGET_CHARS
        assert ok
        assert "PAGING / TRUNCATION NOTICE" in block, "long file must carry a paging notice"
        assert "not been shown" in block.lower()
        assert ":more" in block
    finally:
        os.unlink(p)
    print("[PASS] a long file's first chunk carries an explicit paging notice")


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
    # Use a tiny custom max_mb so we don't have to write 50 MB.
    big = b"x" * (2 * 1024 * 1024)  # 2 MB
    p = _tmp(big, ".txt")
    try:
        ok, msg, _ = fr.load_file(p, max_mb=1)   # 1 MB limit
        assert not ok and "over the" in msg.lower() and "limit" in msg.lower()
    finally:
        os.unlink(p)
    print("[PASS] oversize file refused before reading (configurable limit)")


def test_default_accept_limit_is_50mb():
    assert fr.DEFAULT_MAX_ATTACH_MB == 50
    assert fr.max_attach_bytes() == 50 * 1024 * 1024
    assert fr.max_attach_bytes(10) == 10 * 1024 * 1024
    print("[PASS] default attach limit is 50 MB, configurable")


def test_budget_scales_with_num_ctx():
    small = fr.budget_chars(None)          # default when unknown
    big = fr.budget_chars(32768)           # large context
    assert big > small, "budget should grow with num_ctx"
    assert fr.budget_chars(0) == fr.DEFAULT_BUDGET_CHARS
    assert fr.budget_chars(100) >= fr.MIN_BUDGET_CHARS, "honest floor on tiny context"
    print("[PASS] per-chunk budget scales with num_ctx, with an honest floor")


def test_read_chunk_paging_advances_and_completes():
    text = "\n".join(f"line {i}" for i in range(5000))
    budget = 2000
    seen = 0
    offset = 0
    chunks = 0
    done = False
    while not done and chunks < 1000:
        c = fr.read_chunk(text, "big.txt", char_offset=offset, budget=budget)
        assert c["next_offset"] > offset or c["done"], "offset must advance"
        seen += c["shown_chars"]
        offset = c["next_offset"]
        done = c["done"]
        chunks += 1
    assert done, "paging must eventually complete"
    assert seen >= len(text) - 5, "paging must cover ~all chars (no silent loss)"
    assert chunks > 1, "a 5000-line file should take multiple chunks at budget=2000"
    print("[PASS] read_chunk pages forward, covers the whole file, then reports done")


def test_partial_chunk_announces_paging():
    text = "\n".join(f"line {i}" for i in range(5000))
    c = fr.read_chunk(text, "big.txt", char_offset=0, budget=2000)
    assert not c["done"]
    assert "PAGING" in c["block"] and ":more" in c["block"]
    assert "NOT been shown" in c["block"]
    print("[PASS] a partial chunk explicitly announces unseen content + :more")


def test_small_file_one_chunk_no_notice():
    c = fr.read_chunk("just a little text\n", "tiny.txt", char_offset=0, budget=8000)
    assert c["done"]
    # The instructional header mentions 'PAGING/TRUNCATION' generically; what must
    # be absent for a one-chunk file is the actual notice marker + the FINAL CHUNK
    # banner (a single complete chunk needs neither).
    assert "NOTICE \u2014" not in c["block"] and "FINAL CHUNK" not in c["block"]
    assert ":more" not in c["block"]
    print("[PASS] a small file fits in one chunk with no paging notice")


def test_suggest_lists_nearby_files():
    import tempfile, pathlib
    d = tempfile.mkdtemp()
    open(os.path.join(d, "voice.py"), "w").close()
    open(os.path.join(d, "session.py"), "w").close()
    try:
        # ask for a missing file in that dir -> error should suggest neighbors
        ok, msg, _ = fr.load_file(os.path.join(d, "voce.py"))  # typo
        assert not ok
        assert "Nearby" in msg and "voice.py" in msg
    finally:
        import shutil; shutil.rmtree(d)
    print("[PASS] not-found error suggests nearby files (did-you-mean)")


def test_parse_read_arg_splits_path_and_question():
    # parser lives in seedling (CLI concern); import and exercise it.
    import seedling
    path, q = seedling._parse_read_arg("~/seedling/voice.py this is what gives you a voice")
    assert path == "~/seedling/voice.py", f"path mis-parsed: {path!r}"
    assert q == "this is what gives you a voice"
    # quoted path with spaces
    path2, q2 = seedling._parse_read_arg('"~/My Notes.txt" summarize it')
    assert path2 == "~/My Notes.txt" and q2 == "summarize it"
    # path only, no question
    path3, q3 = seedling._parse_read_arg("~/foo.py")
    assert path3 == "~/foo.py" and q3 is None
    print("[PASS] :read arg splits into (path, question), quote-aware")


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
