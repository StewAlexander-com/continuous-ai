#!/usr/bin/env python3
"""Caution and speak_bias disposition lines stay orthogonal."""
import sys

sys.path.insert(0, ".")
import caution as C
import voice as V


def test_caution_line_no_speak_leak():
    for band in (C.CautionBand.GUARDED, C.CautionBand.RESTRAINED, C.CautionBand.DECLINE_FIRST):
        line = C.prompt_line(band).lower()
        assert "speak the speakable" not in line
        assert "tts" not in line
    print("ok: caution lines do not mention speak-bias mechanics")


def test_speak_bias_no_hedge_leak():
    line = V.speak_bias_line().lower()
    assert "decline-first" not in line
    assert "assertion restraint" not in line
    assert "hedge" not in line
    print("ok: speak_bias line does not mention caution/hedge")


def test_cross_ref_only_when_both():
    solo = C.prompt_line(C.CautionBand.RESTRAINED, speak_bias_active=False).lower()
    both = C.prompt_line(C.CautionBand.RESTRAINED, speak_bias_active=True).lower()
    assert "speaking disposition" not in solo
    assert "speaking disposition" in both
    print("ok: cross-reference appears only when speak_bias active")


if __name__ == "__main__":
    test_caution_line_no_speak_leak()
    test_speak_bias_no_hedge_leak()
    test_cross_ref_only_when_both()
    print("\nALL ORTHOGONALITY TESTS PASSED")
