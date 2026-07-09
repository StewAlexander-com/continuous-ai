"""
test_inputsafe.py — hardened multi-line input.

Proves the sanitizer neutralizes hostile paste content (terminal escapes,
control bytes, trojan-source Unicode, oversize dumps) WHILE preserving
legitimate code/CSV/Python verbatim — and that the multi-line reader has safe
submit/cancel/EOF/non-TTY semantics and never lets a pasted block run a command.
"""
import sys
sys.path.insert(0, ".")
import inputsafe as I

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


# ---------------- sanitize_input: hostile content ----------------
def test_strips_ansi_escapes():
    hostile = "hello \x1b[31mRED\x1b[0m \x1b]0;pwned\x07 world"
    out, notices = I.sanitize_input(hostile)
    check("ANSI/OSC escapes removed", "\x1b" not in out and "[31m" not in out)
    check("visible text survives", "hello" in out and "RED" in out and "world" in out)
    check("escape removal is announced", any("escape" in n for n in notices))


def test_strips_control_bytes_keeps_tab_newline():
    hostile = "a\x00b\x07c\td\ne"   # NUL, BEL stripped; TAB, LF kept
    out, notices = I.sanitize_input(hostile)
    check("NUL/BEL removed", "\x00" not in out and "\x07" not in out)
    check("TAB preserved", "\t" in out)
    check("newline preserved", "\n" in out)
    check("control removal announced", any("control" in n for n in notices))


def test_strips_trojan_source_unicode():
    hostile = "transfer \u202e funds \u200b now"   # bidi override + zero-width
    out, notices = I.sanitize_input(hostile)
    check("bidi override removed", "\u202e" not in out)
    check("zero-width removed", "\u200b" not in out)
    check("hidden-unicode removal announced", any("hidden" in n or "bidir" in n for n in notices))


def test_oversize_truncates_loudly():
    big = "x" * (I.MAX_CHARS + 5000)
    out, notices = I.sanitize_input(big)
    check("truncated to cap", len(out) == I.MAX_CHARS)
    check("truncation announced with count", any("truncated" in n and "characters" in n for n in notices))


def test_too_many_lines_truncates():
    many = "\n".join(str(i) for i in range(I.MAX_LINES + 100))
    out, notices = I.sanitize_input(many)
    check("line cap enforced", out.count("\n") <= I.MAX_LINES)
    check("line truncation announced", any("lines" in n for n in notices))


def test_crlf_normalized():
    out, _ = I.sanitize_input("a\r\nb\rc")
    check("CRLF/CR normalized to LF", out == "a\nb\nc")


def test_never_raises_on_bytes_or_none():
    o1, _ = I.sanitize_input(None)
    o2, _ = I.sanitize_input(b"raw \xff bytes")   # invalid utf-8
    check("None -> empty", o1 == "")
    check("invalid bytes don't raise", isinstance(o2, str) and "raw" in o2)


# ---------------- sanitize_input: legitimate content survives ----------------
def test_python_code_survives_intact():
    code = 'def f(x):\n\tif x > 0:\n\t\treturn "ok"  # comment\n\treturn None\n'
    out, notices = I.sanitize_input(code)
    check("python code unchanged", out.rstrip("\n") == code.rstrip("\n"))
    check("clean code produces no notices", notices == [])


def test_csv_survives_intact():
    csv = "name,score,note\nAida,1.0,\"won't fabricate\"\nx,0.5,plain\n"
    out, notices = I.sanitize_input(csv)
    check("csv unchanged", out.rstrip("\n") == csv.rstrip("\n"))
    check("csv produces no notices", notices == [])


def test_unicode_text_preserved():
    s = "café — naïve — Gödel — 日本語 — 10^120"
    out, notices = I.sanitize_input(s)
    check("legit unicode preserved", "Gödel" in out and "日本語" in out and "café" in out)
    check("legit unicode: no notices", notices == [])


# ---------------- read_multiline: loop semantics ----------------
def _feeder(lines):
    it = iter(lines)
    def fake_input(prompt=""):
        try: return next(it)
        except StopIteration: raise EOFError
    return fake_input


def test_single_line_returns_immediately_no_double_enter():
    # THE REGRESSION FIX: one typed line returns at once, no blank-line wait.
    out = I.read_multiline(_input=_feeder(["Good morning Aida"]),
                           _isatty=True, _drain=lambda: [])
    check("single typed line returns immediately", out == "Good morning Aida")


def test_paste_block_drained_in_one_shot():
    # A paste: first line via input(), the rest already buffered -> _drain.
    out = I.read_multiline(_input=_feeder(["line one"]), _isatty=True,
                           _drain=lambda: ["line two", "line three"])
    check("pasted block assembled from buffer", out == "line one\nline two\nline three")


def test_empty_first_line_is_empty_turn():
    out = I.read_multiline(_input=_feeder([""]), _isatty=True, _drain=lambda: [])
    check("empty first line -> empty turn", out == "")


def test_single_line_command_returns_immediately():
    out = I.read_multiline(_input=_feeder([":model llama3"]), _isatty=True,
                           _drain=lambda: [])
    check("command returned as single line", out == ":model llama3")
    check("looks_like_command true for :model", I.looks_like_command(":model llama3"))
    check("looks_like_command true for exit", I.looks_like_command("exit"))
    check("looks_like_command false for chat", not I.looks_like_command("what is the second arrow"))


def test_eof_on_first_line_raises():
    raised = False
    try:
        I.read_multiline(_input=_feeder([]), _isatty=True, _drain=lambda: [])
    except EOFError:
        raised = True
    check("EOF on first line raises (exit)", raised)


def test_command_only_matches_exact_single_line():
    # The security property: a line that merely CONTAINS a command word but has
    # trailing content is NOT a command (so ':model evil' style can't be faked,
    # and a chat sentence starting with 'exit ...' isn't a quit).
    check("'exit the building' is not a quit command",
          not I.looks_like_command("exit the building"))
    check("':readme' is not the :read command", not I.looks_like_command(":readme"))
    check("':model' bare IS a command", I.looks_like_command(":model"))
    check("':model llama' IS a command", I.looks_like_command(":model llama"))
    check("':help' IS a command", I.looks_like_command(":help"))
    check("':setup' IS a command", I.looks_like_command(":setup"))
    check("':voice chatty' IS a command", I.looks_like_command(":voice chatty"))


def test_seedling_gate_blocks_commands_in_multiline(_unused=None):
    # Defense in depth lives in seedling.py: even if a block's first line looked
    # like a command, the REPL only dispatches commands when is_single_line.
    # Here we assert the helper contract read_multiline relies on: a TYPED
    # single 'exit' is a quit (expected UX), but a block is multi-line text.
    typed = I.read_multiline(_input=_feeder(["exit"]), _isatty=True, _drain=lambda: [])
    check("typed 'exit' alone returns 'exit' (single-line quit, expected)", typed == "exit")
    block = I.read_multiline(_input=_feeder(["please summarize:"]), _isatty=True,
                             _drain=lambda: ["exit codes 0-255"])
    check("a pasted block stays multi-line text", "\n" in block and block.startswith("please summarize"))


if __name__ == "__main__":
    for fn in [
        test_strips_ansi_escapes, test_strips_control_bytes_keeps_tab_newline,
        test_strips_trojan_source_unicode, test_oversize_truncates_loudly,
        test_too_many_lines_truncates, test_crlf_normalized,
        test_never_raises_on_bytes_or_none, test_python_code_survives_intact,
        test_csv_survives_intact, test_unicode_text_preserved,
        test_single_line_returns_immediately_no_double_enter,
        test_paste_block_drained_in_one_shot,
        test_empty_first_line_is_empty_turn,
        test_single_line_command_returns_immediately,
        test_eof_on_first_line_raises,
        test_command_only_matches_exact_single_line,
        test_seedling_gate_blocks_commands_in_multiline,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
