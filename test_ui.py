"""
test_ui.py — the themed console module (identity prefix, palettes, NO_COLOR).

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
    ui.set_theme(ui.DEFAULT_THEME)


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
    check("FORCE_COLOR b&w: warn is bold, not yellow", ui.warn("h").startswith("\033[1m"))
    check("FORCE_COLOR b&w: warn has no hue", "38;2" not in ui.warn("h") and "\033[33m" not in ui.warn("h"))
    check("FORCE_COLOR b&w: speaker stays plain", ui.speaker_prefix() == "Aida: ")


def test_spinner_clear_always_emits():
    # Screen control must emit regardless of color setting (else the spinner
    # animation lingers on screen).
    _reload(NO_COLOR="1")
    check("clear_full_line emits \\033[2K even under NO_COLOR", "\033[2K" in ui.clear_full_line())


def test_terminal_columns_honors_explicit_width():
    check("width override", ui.terminal_columns(60) == 60)
    check("width floor", ui.terminal_columns(10) == 40)


def test_reply_stream_writer_wraps_narrow_prose():
    import io

    buf = io.StringIO()
    w = ui.ReplyStreamWriter(buf, width=40)
    w.feed("This is a longer reply that should wrap cleanly on a narrow terminal.")
    w.finish()
    out = buf.getvalue()
    check("prefix present", out.startswith("\nAida: "))
    check("wraps to multiple lines", out.count("\n") >= 2)
    check("continuation indent", "      " in out)
    check("content preserved", "narrow terminal" in out.replace("\n", " "))


def test_reply_stream_writer_preserves_code_fence_lines():
    import io

    text = "Before.\n```python\nprint('hello world')\nx = 1\n```\nAfter."
    buf = io.StringIO()
    w = ui.ReplyStreamWriter(buf, width=30)
    w.feed(text)
    w.finish()
    out = buf.getvalue()
    check("code line intact", "print('hello world')" in out)
    check("fence markers kept", "```python" in out and "```" in out)


def test_format_wrapped_reply_matches_stream():
    text = "Short answer."
    streamed = ui.format_wrapped_reply(text, width=72)
    check("has speaker prefix", streamed.startswith("\nAida: "))
    check("text preserved", "Short answer." in streamed)


def test_reply_stream_writer_waits_for_word_boundary():
    import io

    buf = io.StringIO()
    w = ui.ReplyStreamWriter(buf, width=30)
    w.feed("misapplic")
    mid = buf.getvalue()
    check("partial word held", "misapplic" not in mid or mid.endswith("misapplic"))
    w.feed("ation of programming")
    w.finish()
    out = buf.getvalue()
    check("no mid-word break", "misapplic\n" not in out and "misapplicatio\n" not in out)
    check("full word present", "misapplication" in out.replace("\n", " "))
    print("[PASS] ReplyStreamWriter waits for word boundaries while streaming")


def test_wrap_hint_lines_prefers_pipe_segments():
    lines = ui.wrap_hint_lines(
        "Type  :help  for commands  |  :status  for health  |  :learning  for how she learns",
        width=55,
    )
    check("multiple segments", len(lines) >= 2)
    check("no orphan she", not any(l.rstrip().endswith(" she") for l in lines))
    check("learning intact", any("learns" in l for l in lines))
    print("[PASS] wrap_hint_lines breaks at | segments")


def test_wrap_plain_lines_for_dim_blocks():
    lines = ui.wrap_plain_lines(
        "A memory confirmation that should not run off the edge on small screens.",
        width=50,
    )
    check("multiple lines", len(lines) >= 2)
    joined = " ".join(l.strip() for l in lines)
    check("full text", "small screens" in joined)


def test_parse_theme_aliases():
    check("theme:dark", ui.parse_theme("theme:dark") == "dark")
    check(":dark", ui.parse_theme(":dark") == "dark")
    check("bw", ui.parse_theme("bw") == "b&w")
    check("light", ui.parse_theme("light") == "light-color")
    check("unknown is None", ui.parse_theme("monokai") is None)
    check("empty is None", ui.parse_theme("") is None)


def test_set_theme_falls_back_to_bw():
    ui.set_theme("nope")
    check("unknown falls back", ui.current_theme() == "b&w")
    ui.set_theme("dark")
    check("dark sticks", ui.current_theme() == "dark")
    ui.set_theme(ui.DEFAULT_THEME)


def test_dark_theme_hues_and_wrap_width():
    import io
    _reload(FORCE_COLOR="1")
    ui.set_theme("dark")
    w = ui.warn("oops")
    p = ui.speaker_prefix()
    o = ui.ok("OK")
    d = ui.dim("hint")
    check("dark warn is bold+truecolor", w.startswith("\033[1m\033[38;2;253;151;31m"))
    check("dark speaker is cyan", "38;2;102;217;239" in p)
    check("dark ok is green", "38;2;166;226;46" in o)
    check("dark chrome is comment gray", "38;2;117;113;94" in d)
    check("colored 32 maps to ok", "38;2;166;226;46" in ui.colored("OK", "32"))
    check("colored 33 maps to warn", "38;2;253;151;31" in ui.colored("x", "33"))
    buf = io.StringIO()
    writer = ui.ReplyStreamWriter(buf, width=40)
    writer.feed("This is a longer reply that should wrap cleanly on a narrow terminal.")
    writer.finish()
    out = buf.getvalue()
    check("dark wrap still names Aida", "Aida:" in out)
    check("visible prefix width unchanged", writer._col <= 40)
    check("continuation indent is 6 spaces", "\n      " in out)
    ui.set_theme(ui.DEFAULT_THEME)


def test_light_color_theme_uses_ink_not_neon():
    _reload(FORCE_COLOR="1")
    ui.set_theme("light-color")
    p = ui.speaker_prefix()
    check("light speaker is darkened cyan", "38;2;10;108;128" in p)
    check("light warn is darkened orange", "38;2;168;90;0" in ui.warn("x"))
    ui.set_theme(ui.DEFAULT_THEME)


def test_no_color_wins_over_dark_theme():
    _reload(NO_COLOR="1")
    ui.set_theme("dark")
    check("NO_COLOR strips warn", "\033" not in ui.warn("oops") and ui.warn("oops") == "oops")
    check("NO_COLOR strips speaker", ui.speaker_prefix() == "Aida: ")
    check("hues off", ui.hues_enabled() is False)
    ui.apply_canvas()
    check("NO_COLOR does not paint canvas", ui.canvas_applied() is False)


def test_apply_canvas_skips_non_tty_even_with_force_color():
    _reload(FORCE_COLOR="1")
    ui.set_theme("dark")
    ui.apply_canvas()
    check("no OSC on non-TTY", ui.canvas_applied() is False)
    ui.release_canvas()
    check("release is idempotent", ui.canvas_applied() is False)
    ui.set_theme(ui.DEFAULT_THEME)


def test_config_ships_bw_default():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    check("shipped theme is b&w", cfg.get("theme") == "b&w")


if __name__ == "__main__":
    test_speaker_is_aida()
    test_no_color_strips_ansi_keeps_text()
    test_force_color_emits_ansi()
    test_spinner_clear_always_emits()
    test_terminal_columns_honors_explicit_width()
    test_reply_stream_writer_wraps_narrow_prose()
    test_reply_stream_writer_preserves_code_fence_lines()
    test_reply_stream_writer_waits_for_word_boundary()
    test_wrap_hint_lines_prefers_pipe_segments()
    test_format_wrapped_reply_matches_stream()
    test_wrap_plain_lines_for_dim_blocks()
    test_parse_theme_aliases()
    test_set_theme_falls_back_to_bw()
    test_dark_theme_hues_and_wrap_width()
    test_light_color_theme_uses_ink_not_neon()
    test_no_color_wins_over_dark_theme()
    test_apply_canvas_skips_non_tty_even_with_force_color()
    test_config_ships_bw_default()
    _reload()  # restore clean env
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
