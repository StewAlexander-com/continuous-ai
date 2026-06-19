#!/usr/bin/env python3
"""
eval_l3_judge.py — blind external-model judge for the L3 A/B eval (#10).

Sends each anonymized (A,B) answer pair to the Perplexity API (sonar) — the same
external backend the project's critic already uses — and asks which answer is
better and more "Aida-shaped". The judge does NOT know which is L3-on. After
judging, we de-blind with the key file and tally how often L3-on won.

This is a DIFFERENT, stronger model judging — not self-grading. It is still a
model, so it's reported alongside (not instead of) your blind human spot-check.

Requires: PERPLEXITY_API_KEY env var (same as critic_backend: perplexity).

Usage:
    ./.venv/bin/python eval_l3_judge.py l3_eval_out/l3_blind_pairs_<ts>.json
    (auto-finds the matching l3_key_<ts>.json next to it)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

JUDGE_MODEL = "sonar"

RUBRIC = """You are a blind evaluator. Two AI assistants answered the same prompt.
The persona they aim for ("Aida") values: leading with a bottom-line (BLUF),
reasoning via explicit mechanisms over vibes, naming the framework in use
(e.g. Stoic "Second Arrow", Gödelian limits, epistemic humility), and expressing
CALIBRATED, EXPLICIT uncertainty rather than vague hedging or false confidence.

Judge which answer is BETTER overall for this user — more useful, more honest
about uncertainty, and more in that reasoning style. Avoid length or formatting
bias; judge substance.

PROMPT:
{prompt}

--- ANSWER A ---
{a}

--- ANSWER B ---
{b}

Return ONLY valid JSON, no markdown:
{{"winner": "A" | "B" | "tie", "reason": "<one sentence>", "confidence": 0.0-1.0}}"""


def _judge_pair(api_key, prompt, a, b):
    import httpx
    content = RUBRIC.format(prompt=prompt, a=a, b=b)
    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise evaluator. Return only valid JSON."},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }
    r = httpx.post("https://api.perplexity.ai/chat/completions",
                   headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                   json=payload, timeout=45.0)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0)) if m else {"winner": "tie", "reason": "unparseable", "confidence": 0.0}


def main():
    if len(sys.argv) < 2:
        print("usage: eval_l3_judge.py <l3_blind_pairs_*.json>")
        sys.exit(1)
    pairs_path = Path(sys.argv[1])
    key_path = Path(str(pairs_path).replace("l3_blind_pairs_", "l3_key_"))
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        print("PERPLEXITY_API_KEY not set. Get one at https://www.perplexity.ai/settings/api")
        sys.exit(2)

    pairs = json.loads(pairs_path.read_text())
    key = json.loads(key_path.read_text())

    on_wins = off_wins = ties = 0
    rows = []
    for p in pairs:
        v = _judge_pair(api_key, p["prompt"], p["answer_A"], p["answer_B"])
        w = v.get("winner", "tie")
        k = key.get(p["pair_id"], {})
        winner_state = k.get(w) if w in ("A", "B") else "tie"
        if winner_state == "on":
            on_wins += 1
        elif winner_state == "off":
            off_wins += 1
        else:
            ties += 1
        rows.append({"pair_id": p["pair_id"], "judge_winner": w,
                     "winner_state": winner_state, "reason": v.get("reason", ""),
                     "confidence": v.get("confidence", 0.0)})
        print(f"  {p['pair_id']:<28} judge={w:<4} -> L3-{winner_state:<4}  ({v.get('reason','')[:70]})")

    n = len(pairs)
    print("\n" + "=" * 60)
    print("  EXTERNAL JUDGE (sonar, blind) — L3 A/B RESULT")
    print("=" * 60)
    print(f"  Pairs judged : {n}")
    print(f"  L3-ON  wins  : {on_wins}  ({on_wins/n:.0%})" if n else "  no pairs")
    print(f"  L3-OFF wins  : {off_wins}  ({off_wins/n:.0%})" if n else "")
    print(f"  Ties         : {ties}  ({ties/n:.0%})" if n else "")
    if n:
        if on_wins > off_wins:
            verdict = f"L3-ON preferred ({on_wins} vs {off_wins})"
        elif off_wins > on_wins:
            verdict = f"L3-OFF preferred ({off_wins} vs {on_wins}) — L3 may HURT; investigate"
        else:
            verdict = "No clear preference — L3 effect not detectable by this judge"
        print(f"  VERDICT      : {verdict}")
    print("=" * 60)
    out = pairs_path.parent / pairs_path.name.replace("l3_blind_pairs_", "l3_judge_result_")
    out.write_text(json.dumps({"n": n, "on_wins": on_wins, "off_wins": off_wins,
                               "ties": ties, "rows": rows}, indent=2))
    print(f"  wrote {out.name}")


if __name__ == "__main__":
    main()
