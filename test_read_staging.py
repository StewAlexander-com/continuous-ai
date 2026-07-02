"""Tests for :read/:more staging — Aida must NOT answer until the user has paged
what they want and asked. Covers the pure turn-composer that folds staged file
chunks into a turn.

Run: ./.venv/bin/python test_read_staging.py
"""
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
    check(rs["staged"] == [], "staged buffer is consumed after folding")


def test_staged_empty_enter_gives_orientation():
    print("\ntest_staged_empty_enter_gives_orientation")
    rs = {"name": "foo.py", "done": True, "staged": ["<<chunk1>>"]}
    turn, submit = S._compose_staged_turn(rs, "")
    check(submit, "empty Enter WITH staged content submits (respond-now signal)")
    check("<<chunk1>>" in turn, "staged content still included")
    check("Briefly say what it is" in turn, "generic orientation ask used when no question")


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


if __name__ == "__main__":
    for fn in (
        test_no_staged_normal_turn,
        test_no_staged_empty_is_noop,
        test_staged_plus_question_folds_and_submits,
        test_staged_empty_enter_gives_orientation,
        test_partial_view_note_when_not_done,
        test_none_read_state_safe,
    ):
        fn()
    print("\n" + "=" * 50)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 50)
    raise SystemExit(1 if _failed else 0)
