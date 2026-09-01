"""Tests for :read/:more staging — Aida must NOT answer until the user has paged
what they want and asked. Covers the pure turn-composer that folds staged file
chunks into a turn.

Run: ./.venv/bin/python test_read_staging.py
"""
import contextlib
import io
import os
import tempfile

import filereader as F
import seedling as S


_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


def test_no_staged_normal_turn():
    print("\ntest_no_staged_normal_turn")
    turn, submit = S._compose_staged_turn({}, "hello there")
    check(turn == "hello there" and submit, "plain turn passes through, submit=True")


def test_no_staged_empty_is_noop():
    print("\ntest_no_staged_empty_is_noop")
    turn, submit = S._compose_staged_turn({}, "")
    check(not submit, "empty input with nothing staged -> submit=False (skip)")


def test_staged_plus_question_folds_and_submits():
    print("\ntest_staged_plus_question_folds_and_submits")
    rs = {"name": "foo.py", "done": True, "staged": ["<<chunk1>>", "<<chunk2>>"]}
    turn, submit = S._compose_staged_turn(rs, "what does this do?")
    check(submit, "submits when staged content + question")
    check("<<chunk1>>" in turn and "<<chunk2>>" in turn, "all staged chunks included, in order")
    check(turn.index("<<chunk1>>") < turn.index("<<chunk2>>"), "chunk order preserved")
    check("what does this do?" in turn, "the user's question is included")
    check("foo.py" in turn, "the file name is referenced")
    check("hypothesize" in turn or "general knowledge" in turn,
          "beyond-doc reasoning explicitly permitted")
    check("Answer only from" not in turn, "must NOT forbid beyond-doc reasoning")
    check("do NOT attribute" in turn or "attribut" in turn.lower(),
          "blocks inventing authorship claims from bare citations")
    check("quote" in turn.lower() or "shown text" in turn, "citation grounding still present")
    check(rs["staged"] == [], "staged buffer is consumed after folding")


def test_staged_empty_enter_gives_orientation():
    print("\ntest_staged_empty_enter_gives_orientation")
    rs = {"name": "foo.py", "done": True, "staged": ["<<chunk1>>"]}
    turn, submit = S._compose_staged_turn(rs, "")
    check(submit, "empty Enter WITH staged content submits (respond-now signal)")
    check("<<chunk1>>" in turn, "staged content still included")
    check("Briefly say what it is" in turn, "generic orientation ask used when no question")
    check("unread" in turn.lower(), "orientation still forbids inventing unread contents")
    check("Answer only from" not in turn, "orientation must not ban beyond-doc help")


def test_partial_view_note_when_not_done():
    print("\ntest_partial_view_note_when_not_done")
    rs = {"name": "big.py", "done": False, "staged": ["<<c1>>"]}
    turn, _ = S._compose_staged_turn(rs, "review it")
    check("partial view" in turn, "notes the view is partial when more of the file remains")
    rs2 = {"name": "big.py", "done": True, "staged": ["<<c1>>"]}
    turn2, _ = S._compose_staged_turn(rs2, "review it")
    check("partial view" not in turn2, "no partial note when the whole file was shown")


def test_none_read_state_safe():
    print("\ntest_none_read_state_safe")
    turn, submit = S._compose_staged_turn(None, "hi")
    check(turn == "hi" and submit, "None read_state behaves like empty (no crash)")


def test_current_file_name_reuses_staged_attachment():
    print("\ntest_current_file_name_reuses_staged_attachment")
    with tempfile.TemporaryDirectory() as d:
        current = os.path.join(d, "Resume.html")
        sibling = os.path.join(d, "Other.html")
        open(current, "w").write("resume")
        open(sibling, "w").write("other")
        rs = {
            "kind": "file",
            "name": "Resume.html",
            "source_path": current,
            "browse_directory": d,
            "done": True,
            "staged": ["<<chunk1>>", "<<chunk2>>"],
        }
        check(S._current_attachment_matches(rs, current),
              "same named child is recognized as current attachment")
        check(not S._current_attachment_matches(rs, sibling),
              "different sibling still routes to a fresh read")
        turn, submit = S._compose_staged_turn(rs, "summarize Resume.html")
        check(submit and "<<chunk1>>" in turn and "<<chunk2>>" in turn,
              "same-file question submits all staged chunks without reload")


def test_more_chunk_numbers_are_monotonic():
    print("\ntest_more_chunk_numbers_are_monotonic")
    text = "\n".join(f"line {i} padding for paging" for i in range(1000))
    first = F.read_chunk(text, "Resume.html", char_offset=0, budget=2000, chunk_no=1)
    rs = {
        "kind": "file", "name": "Resume.html", "text": text,
        "offset": first["next_offset"], "total": first["total"],
        "budget": 2000, "done": first["done"], "chunk_no": 1,
        "staged": [first["block"]],
    }
    with contextlib.redirect_stdout(io.StringIO()):
        S._handle_more_command(None, rs)
    check(rs["chunk_no"] == 2, "first :more is chunk 2, not chunk 1 again")
    with contextlib.redirect_stdout(io.StringIO()):
        S._handle_more_command(None, rs)
    check(rs["chunk_no"] == 3, "second :more advances to chunk 3")


def test_failed_pick_redraws_renumbered_menu():
    print("\ntest_failed_pick_redraws_renumbered_menu")
    with tempfile.TemporaryDirectory() as d:
        paths = [os.path.join(d, name) for name in ("a.txt", "b.bin", "c.txt")]
        for path in paths:
            open(path, "wb").write(b"x")
        pick = {
            "mode": "directory",
            "candidates": paths[:],
            "labels": ["a.txt", "b.bin", "c.txt"],
            "attempted": d,
        }
        read = {"kind": "directory", "source_path": d, "text": "listing"}
        original = S._handle_read_command
        try:
            # Simulate an existing file that fails to attach.
            S._handle_read_command = lambda *args, **kwargs: None
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = S._try_read_pick_turn(
                    "2", pick, None, {}, read)
        finally:
            S._handle_read_command = original
        check(result == "handled", "failed pick is consumed")
        check(pick["candidates"] == [paths[0], paths[2]],
              "failed existing pick is removed")
        check(pick["labels"] == ["a.txt", "c.txt"],
              "labels stay aligned after removal")
        rendered = output.getvalue()
        check("menu" in rendered.lower() and "c.txt" in rendered,
              "updated numbering is visibly redrawn")


def test_conversational_read_puts_bytes_in_this_turn():
    print("\ntest_conversational_read_puts_bytes_in_this_turn")
    fd, path = tempfile.mkstemp(suffix=".md")
    os.write(fd, b"# SNR\nhello from the attached file\n")
    os.close(fd)
    streamed = []
    original = S._stream_turn

    def capture(session, turn_text, **kwargs):
        streamed.append(turn_text)

    S._stream_turn = capture
    rs = {}
    try:
        line = f"just read :read {path}"
        cmd = S._nl_read_command(line)
        check(cmd is not None, "mixed just-read :read line becomes a :read command")
        S._handle_read_command(None, cmd, {}, rs)
        check(len(streamed) == 1, "conversational read streams THIS turn")
        check("hello from the attached file" in streamed[0],
              "file bytes are in the model turn")
        check("just read" in streamed[0].lower(),
              "the user's ask is in the turn so conversation continues")
        check(not rs.get("staged"), "immediate answer consumes the staged chunk")
    finally:
        S._stream_turn = original
        os.unlink(path)


if __name__ == "__main__":
    for fn in (
        test_no_staged_normal_turn,
        test_no_staged_empty_is_noop,
        test_staged_plus_question_folds_and_submits,
        test_staged_empty_enter_gives_orientation,
        test_partial_view_note_when_not_done,
        test_none_read_state_safe,
        test_current_file_name_reuses_staged_attachment,
        test_more_chunk_numbers_are_monotonic,
        test_failed_pick_redraws_renumbered_menu,
        test_conversational_read_puts_bytes_in_this_turn,
    ):
        fn()
    print("\n" + "=" * 50)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 50)
    raise SystemExit(1 if _failed else 0)
