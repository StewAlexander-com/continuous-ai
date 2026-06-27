"""
test_ui.py — the themed console module (identity prefix + NO_COLOR).

Locks in the identity-bundle contract:
  - The reply speaker is 'Aida', not 'Model' (the contradiction this fixed).
  - NO_COLOR / non-TTY strips ANSI; the TEXT always survives.
  - Screen-control codes for the spinner erase always emit (correctness).
"""
import importlib
import os
import sys

sys.path.insert(0, ".")
import ui

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


def _reload(**env):
    for k in ("NO_COLOR", "FORCE_COLOR"):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v
    importlib.reload(ui)


def test_speaker_is_aida():
    check("reply prefix names Aida, not Model", ui.SPEAKER == "Aida")
    check("inline prefix is '\\nAida: '", ui.reply_prefix_inline() == "\nAida: ")
    check("no 'Model' anywhere in the prefix", "Model" not in ui.reply_prefix_inline())


def test_no_color_strips_ansi_keeps_text():
    _reload(NO_COLOR="1")
    d = ui.dim("hello"); w = ui.warn("oops"); c = ui.colored("x", "32")
    check("NO_COLOR: dim has no escape", "\033" not in d and d == "hello")
    check("NO_COLOR: warn has no escape", "\033" not in w and w == "oops")
    check("NO_COLOR: colored has no escape", "\033" not in c and c == "x")


def test_force_color_emits_ansi():
    _reload(FORCE_COLOR="1")
    check("FORCE_COLOR: dim wraps in \\033[2m", ui.dim("h").startswith("\033[2m"))
    check("FORCE_COLOR: warn is yellow", ui.warn("h").startswith("\033[33m"))


def test_spinner_clear_always_emits():
    # Screen control must emit regardless of color setting (else the spinner
    # animation lingers on screen).
    _reload(NO_COLOR="1")
    check("clear_full_line emits \\033[2K even under NO_COLOR", "\033[2K" in ui.clear_full_line())


if __name__ == "__main__":
    test_speaker_is_aida()
    test_no_color_strips_ansi_keeps_text()
    test_force_color_emits_ansi()
    test_spinner_clear_always_emits()
    _reload()  # restore clean env
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
