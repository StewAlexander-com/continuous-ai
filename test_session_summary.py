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
    lines = ui.summary_field_lines("Emergent detail", detail, width=72)
    check("multiple lines when needed", len(lines) >= 2)
    joined = " ".join(l.replace("Emergent detail:", "").strip() for l in lines)
    joined = joined.replace("  ", " ")
    check("full text preserved in wrap", "unique niche" in joined)
    check("first line has label", lines[0].startswith("  Emergent detail:"))


if __name__ == "__main__":
    test_extract_emergent_detail_stops_at_next_section()
    test_extract_emergent_detail_honest_ellipsis()
    test_summary_field_lines_wraps_without_truncating_mid_word()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
