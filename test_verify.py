#!/usr/bin/env python3
"""Unit tests for gated thin CoVe (verify.py) — no model required."""
import verify as V


def test_buffer_gate():
    assert V.should_buffer_for_cove(enabled=False, applied_d=0.99) is False
    assert V.should_buffer_for_cove(enabled=True, applied_d=0.50) is False
    assert V.should_buffer_for_cove(enabled=True, applied_d=0.68) is True
    assert V.should_buffer_for_cove(enabled=True, applied_d=0.90) is True
    print("ok: buffer gate")


def test_skip_short_and_refusal():
    assert V.should_revise_draft("short") is False
    refuse = (
        "I can't reach that URL. Please paste the contents or attach the file "
        "with :read and I'll reason over what you provide."
    )
    assert V.should_revise_draft(refuse) is False
    invent = (
        "Sure — looking at the GitHub repo, the README says version 9.1 and "
        "there are 42 open issues covering authentication bugs in the OAuth "
        "flow documented on line 88 of CONTRIBUTING.md."
    )
    assert V.should_revise_draft(invent) is True
    print("ok: revise skip heuristics")


def test_disabled_and_below_gate_keep_draft():
    draft = "Invented contents of a remote file that was never attached."
    out, rep = V.revise_draft("q", draft, lambda m, msgs: "SHOULD NOT RUN", "m",
                              enabled=False, applied_d=0.9)
    assert out == draft and rep.skipped_reason == "disabled"
    out, rep = V.revise_draft("q", draft, lambda m, msgs: "SHOULD NOT RUN", "m",
                              enabled=True, applied_d=0.2)
    assert out == draft and rep.skipped_reason == "below_gate"
    print("ok: disabled / below gate")


def test_fail_safe_on_chat_error():
    draft = (
        "Here are the full contents of https://example.com/secret which I "
        "fetched for you with absolute confidence and many invented details."
    )

    def boom(model, messages):
        raise RuntimeError("ollama down")

    out, rep = V.revise_draft("show me the site", draft, boom, "m",
                              enabled=True, applied_d=0.9)
    assert out == draft
    assert rep.ran and rep.error and not rep.replaced
    print("ok: fail-safe keeps draft")


def test_rejects_verifier_confab():
    draft = (
        "The repository README claims thirty contributors and a v2.0 release "
        "with no attachment present in the user message at all."
    )

    def bad_verify(model, messages):
        return "I verified online that the repo has 30 contributors."

    out, rep = V.revise_draft("what's in the repo?", draft, bad_verify, "m",
                              enabled=True, applied_d=0.9)
    assert out == draft
    assert rep.skipped_reason == "verifier_confab"
    print("ok: verifier confab rejected")


def test_replaces_with_honest_revise():
    draft = (
        "I opened https://github.com/acme/widget and the README says the API "
        "key must be set in /etc/secrets which I confirmed by browsing."
    )
    revised = (
        "I can't reach GitHub from here. Paste the README or attach it with "
        ":read and I will reason over what you provide."
    )

    def good(model, messages):
        assert messages[0]["role"] == "system"
        return revised

    out, rep = V.revise_draft("summarize that repo", draft, good, "m",
                              enabled=True, applied_d=0.9)
    assert out == revised
    assert rep.ran and rep.replaced
    print("ok: honest revise accepted")


def test_skips_refusal_draft_without_call():
    draft = (
        "I can't reach that URL. Please paste or attach the page and I'll "
        "work from what you give me."
    )
    calls = {"n": 0}

    def count(model, messages):
        calls["n"] += 1
        return "x"

    out, rep = V.revise_draft("fetch it", draft, count, "m",
                              enabled=True, applied_d=0.9)
    assert out == draft and calls["n"] == 0 and rep.skipped_reason == "draft_skip"
    print("ok: refusal draft skips side call")


def test_verify_prompt_keeps_beyond_doc_reasoning():
    msgs = V._build_verify_messages("suggest pathways", "draft")
    blob = msgs[1]["content"]
    assert "KEEP labeled hypotheses" in blob
    assert "Remove or rewrite ANY specific external fact" not in blob
    assert "pretended browse" in blob or "dishonest reach" in blob
    print("ok: verify prompt permits beyond-doc reasoning")


if __name__ == "__main__":
    test_buffer_gate()
    test_skip_short_and_refusal()
    test_disabled_and_below_gate_keep_draft()
    test_fail_safe_on_chat_error()
    test_rejects_verifier_confab()
    test_replaces_with_honest_revise()
    test_skips_refusal_draft_without_call()
    test_verify_prompt_keeps_beyond_doc_reasoning()
    print("all verify tests passed")
