"""Session-end summary: emergent detail extraction + console wrapping."""
import sys

sys.path.insert(0, ".")
import session as sess
import ui

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


def test_extract_emergent_detail_stops_at_next_section():
    text = (
        "BLUF: intro\n\n"
        "[EMERGENT] The resume reflects your 20+ years experience well, but as a "
        "security engineer who builds offline-first tools, it currently emphasizes "
        "enterprise network engineering more than your unique niche.\n\n"
        "[Gödel's incompleteness note] No resume can be complete."
    )
    detail = sess.extract_emergent_detail(text)
    check("captures full emergent sentence", "20+ years" in detail)
    check("stops before Gödel section", "Gödel" not in detail)
    check("not hard-capped at 80 chars", len(detail) > 80)


def test_extract_emergent_detail_honest_ellipsis():
    long_obs = "[EMERGENT] " + ("word " * 200)
    detail = sess.extract_emergent_detail(long_obs, max_chars=120)
    check("ellipsis when over cap", detail.endswith("…"))
    check("respects max cap", len(detail) <= 121)


def test_summary_field_lines_wraps_without_truncating_mid_word():
    detail = (
        "The resume reflects your 20+ years experience well, but as a security "
        "engineer who builds offline-first tools, it currently emphasizes enterprise "
        "network engineering more than your unique niche."
    )
    lines = ui.summary_field_lines("Emergent", detail, width=72)
    check("multiple lines when needed", len(lines) >= 2)
    joined = " ".join(l.replace("Emergent:", "").strip() for l in lines)
    joined = joined.replace("  ", " ")
    check("full text preserved in wrap", "unique niche" in joined)
    check("first line has label", lines[0].startswith("  Emergent:"))


def test_insight_logged_wraps_without_80_char_cut():
    insight = (
        'User\'s "thanks" after concise response confirms natural closing point '
        "only when the prior answer was complete — not a prompt to continue."
    )
    lines = ui.summary_field_lines("Insight logged", insight, width=72)
    check("insight uses multiple lines when long", len(lines) >= 2)
    joined = " ".join(l.replace("Insight logged:", "").strip() for l in lines)
    joined = joined.replace("  ", " ")
    check("insight not cut at 80 chars", "not a prompt to continue" in joined)
    check("closing phrase intact", "only when the prior answer" in joined)


def test_print_session_end_summary_emergent_false():
    class _Delta:
        insight_gained = "Short insight."
        coherence_score = 1.0
        emergent = False
        emergent_detail = ""

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        ui.print_session_end_summary(_Delta())
    out = buf.getvalue()
    check("shows emergent false", "Emergent       : False" in out)
    check("wraps insight label", "Insight logged:" in out)


def test_print_session_end_summary_emergent_wraps_detail():
    class _Delta:
        insight_gained = "x" * 90
        coherence_score = 0.9
        emergent = True
        emergent_detail = (
            "Unexpected pivot to meta-commentary about session boundaries "
            "after the user said thanks."
        )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        ui.print_session_end_summary(_Delta(), end_summary={"deliberations": 3})
    out = buf.getvalue()
    check("no separate emergent detail label", "Emergent detail:" not in out)
    check("emergent detail under Emergent", "Unexpected pivot" in out)
    check("full insight not truncated at 80", "x" * 90 in out.replace("\n", "").replace(" ", ""))
    check("internal work shown", "3 deliberation(s)" in out)


if __name__ == "__main__":
    test_extract_emergent_detail_stops_at_next_section()
    test_extract_emergent_detail_honest_ellipsis()
    test_summary_field_lines_wraps_without_truncating_mid_word()
    test_insight_logged_wraps_without_80_char_cut()
    test_print_session_end_summary_emergent_false()
    test_print_session_end_summary_emergent_wraps_detail()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
