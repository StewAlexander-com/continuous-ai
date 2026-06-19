#!/usr/bin/env python3
"""
eval_l3_ab.py — A/B eval: does L3 self-shaping cognition change Aida's output?

DESIGN (built to be able to say "no difference" or "worse" — not just to confirm):

  GATE (#5)       Do the two states even produce different output? If byte-identical,
                  L3 is inert and we say so. Cheap falsification, runs first.
  STYLE (#3/#6)   Deterministic style-adherence scoring on the probe set
                  (framework presence, BLUF-lead, explicit-uncertainty markers).
                  Measures the axis L3 actually changes — NOT a quality verdict.
  GUARDRAIL (#7)  Re-run the confab battery with L3-on to confirm we did NOT
                  regress the 0% honesty result ("must not be regressive — absolute").
  QUALITY         Blind pairs exported for: (a) external-model judge (eval_l3_judge.py)
                  and (b) human spot-check (eval_l3_review_*.txt). "Better?" is judged
                  blind, never self-graded.

TWO STATES, ONE DIFFERENCE:
  L3-OFF : ContextState with DEFAULT cognitive_style + persistent_priors (frozen).
  L3-ON  : SAME state but style/priors backfilled from the REAL stored deltas
           (consolidation.consolidate_history). Persona + beliefs identical in both.
  Both system prompts are built by the SHIPPED mcm._format_context_injection —
  we test the real injection path, not a reconstruction.

Safety: reads your real deltas to build L3-ON, but runs against throwaway in-memory
states and NEVER writes .seedling_db. Output files go to ./l3_eval_out/.

Usage (on the Mac, with Ollama running qwen3:30b-a3b):
    ./.venv/bin/python eval_l3_ab.py                # uses config.yaml model
    ./.venv/bin/python eval_l3_ab.py --runs 2       # average style scores over N runs
    ./.venv/bin/python eval_l3_ab.py --no-guardrail # skip the confab re-check (faster)
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

import consolidation as C
import mcm
import storage
from schemas import ContextState, CognitiveStyle, PersistentPriors
from eval_l3_probes import all_probes

OUT = Path(__file__).parent / "l3_eval_out"


def _model_name(override):
    if override:
        return override
    try:
        return (yaml.safe_load(open("config.yaml")) or {}).get("model_name", "llama3.2")
    except Exception:
        return "llama3.2"


def _load_real_state(synthetic: bool = False) -> ContextState:
    """Load the real stored state (for persona, beliefs, and the deltas that
    drive L3-ON).

    synthetic=True: NEVER touches the real DB at all (no init_db, no load_latest)
    — builds L3-ON from built-in synthetic deltas. Use this if you want zero
    contact with .seedling_db, even read-only.

    synthetic=False (default): reads the real state READ-ONLY via load_latest()
    and immediately deepcopies it; the eval never writes back. Falls back to
    synthetic automatically if no DB exists (sandbox dry-run)."""
    if synthetic:
        print("  [--synthetic] not reading .seedling_db; using built-in synthetic deltas.")
        return _synthetic_state()
    try:
        storage.init_db()
        st = storage.load_latest()
        if st is not None:
            return st
    except Exception as e:
        print(f"  (no live DB: {e}; using synthetic dry-run state)")
    return _synthetic_state()


def _synthetic_state() -> ContextState:
    """A tiny, self-contained state for synthetic/dry-run mode. Mirrors the real
    framework profile so L3-ON looks representative, but contains NO real data."""
    from schemas import ThreadDelta
    st = ContextState()
    fw = ([["Second Arrow"]] * 5 + [["BLUF"]] * 5 + [["Gödel's incompleteness"]] * 3
          + [["Seth Lloyd's computational universe"]] * 2 + [["Epistemic Humility"]] * 2)
    st.thread_deltas = [
        ThreadDelta(insight_gained="synthetic", coherence_score=0.85,
                    weight_adjustment_signal=0.3, frameworks_used=f) for f in fw
    ]
    return st


def _build_states(real: ContextState):
    """Return (off_state, on_state). Identical persona/beliefs/deltas; the ONLY
    difference is L3 (default vs backfilled)."""
    off = copy.deepcopy(real)
    off.cognitive_style = CognitiveStyle()          # frozen defaults
    off.persistent_priors = PersistentPriors()

    on = copy.deepcopy(real)
    on.cognitive_style = CognitiveStyle()
    on.persistent_priors = PersistentPriors()
    rep = C.consolidate_history(on.cognitive_style, on.persistent_priors, on.thread_deltas)
    return off, on, rep


def _system_prompt(state: ContextState) -> str:
    """Use the SHIPPED injection path so we test reality, not a mock."""
    return mcm._format_context_injection(state, query="")


class ModelUnavailable(RuntimeError):
    """Raised when the model can't be reached. We ABORT rather than score errors
    as data — a connection failure is not a 'result' and must never look like one."""


def _preflight(model):
    """Fail loudly BEFORE any probe if Ollama isn't reachable or the model is
    missing. The original defect: connection errors were captured as answer text,
    making every pair 'identical' and every guardrail case 'confabulate' —
    dangerously misleading. This makes that impossible."""
    try:
        import ollama
    except ImportError:
        raise ModelUnavailable("ollama package not installed (pip install ollama).")
    try:
        tags = ollama.list()
    except Exception as e:
        raise ModelUnavailable(
            f"Ollama not reachable ({e}). Start it first:  ollama serve  "
            "(or open the Ollama app), then re-run.")
    names = {m.get("model") or m.get("name", "") for m in tags.get("models", [])}
    base = model.split(":")[0]
    if not any(n == model or n.split(":")[0] == base for n in names):
        raise ModelUnavailable(
            f"Model '{model}' not found in Ollama. Available: {sorted(n for n in names if n)}. "
            f"Pull it:  ollama pull {model}")
    # One tiny live call to confirm generation actually works.
    try:
        ollama.chat(model=model, messages=[{"role": "user", "content": "ping"}])
    except Exception as e:
        raise ModelUnavailable(f"Model '{model}' present but generation failed: {e}")


def _strip_think(text: str) -> str:
    """qwen3 and other reasoning models emit <think>...</think> blocks. The scored
    answer is what the user would SEE — strip the hidden reasoning so style/honesty
    markers are matched against the actual reply, not internal monologue."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _ask(model, system_prompt, user_prompt):
    """Returns the model's visible answer. Raises ModelUnavailable on any call
    error — we never turn an error string into scoreable 'data'."""
    import ollama
    try:
        r = ollama.chat(model=model, messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return _strip_think(r["message"]["content"])
    except Exception as e:
        raise ModelUnavailable(f"model call failed mid-run: {e}")


def _count(markers, text):
    t = text.lower()
    return sum(1 for m in markers if re.search(m, t, re.IGNORECASE | re.MULTILINE))


def _style_score(probe, text):
    """Deterministic style-adherence: how 'Aida-shaped' is this answer?
    Returns a dict of component counts + a composite (NOT a quality claim)."""
    fw = _count(probe.framework_markers, text)
    bluf = _count(probe.bluf_markers, text)
    unc = _count(probe.explicit_unc, text)
    return {
        "frameworks": fw,
        "bluf": min(bluf, 1),          # presence, not volume
        "explicit_uncertainty": unc,
        "composite": fw + min(bluf, 1) + min(unc, 3),
    }


def run(model, runs=1, guardrail=True, synthetic=False):
    OUT.mkdir(exist_ok=True)
    real = _load_real_state(synthetic=synthetic)
    off, on, rep = _build_states(real)

    print("=" * 64)
    print("  L3 A/B EVAL — does self-shaping cognition change output?")
    print("=" * 64)
    mode = "SYNTHETIC (no DB access)" if synthetic else "real state (read-only)"
    print(f"  Model: {model}   probes: {len(all_probes())}   runs/probe: {runs}   source: {mode}")
    src_label = "synthetic" if synthetic else "real"
    print(f"\n  L3-ON state was backfilled from {src_label} deltas:")
    print("    " + "\n    ".join(rep.render().splitlines()[3:11]))
    print()

    # Preflight: confirm the model is actually reachable BEFORE scoring anything.
    # Without this, connection failures get captured as answer text and produce
    # bogus 'all identical' + '100% confab' results (the original defect).
    print("  [preflight] checking Ollama + model reachable...", flush=True)
    _preflight(model)
    print("  [preflight] OK — model responds.\n")

    off_sys, on_sys = _system_prompt(off), _system_prompt(on)
    # Sanity: the two system prompts MUST differ (else the test is meaningless).
    prompts_differ = off_sys != on_sys
    print(f"  [gate-0] system prompts differ between states: {prompts_differ}")
    if not prompts_differ:
        print("  ABORT: L3 produced no change in the injected prompt. Feature is inert.")
        return

    records = []
    identical = 0
    agg = {"off": {"composite": 0, "frameworks": 0, "bluf": 0, "explicit_uncertainty": 0},
           "on": {"composite": 0, "frameworks": 0, "bluf": 0, "explicit_uncertainty": 0}}

    for probe in all_probes():
        for run_i in range(runs):
            a_off = _ask(model, off_sys, probe.prompt)
            a_on = _ask(model, on_sys, probe.prompt)
            if a_off.strip() == a_on.strip():
                identical += 1
            s_off, s_on = _style_score(probe, a_off), _style_score(probe, a_on)
            for k in agg["off"]:
                agg["off"][k] += s_off[k]
                agg["on"][k] += s_on[k]
            records.append({
                "probe": probe.id, "run": run_i, "prompt": probe.prompt,
                "off_answer": a_off, "on_answer": a_on,
                "off_style": s_off, "on_style": s_on,
            })

    n = len(records)
    print(f"\n  [gate-1] byte-identical answer pairs: {identical}/{n}")
    if identical == n:
        print("  WARNING: every pair identical despite differing prompts — L3 had no")
        print("           behavioral effect on this model. Honest result: no difference.")

    print("\n  --- STYLE ADHERENCE (deterministic; higher = more 'Aida-shaped') ---")
    print(f"    {'metric':<22} {'L3-OFF':>8} {'L3-ON':>8} {'delta':>8}")
    for k in ["frameworks", "bluf", "explicit_uncertainty", "composite"]:
        o, n_ = agg["off"][k], agg["on"][k]
        print(f"    {k:<22} {o:>8} {n_:>8} {n_-o:>+8}")
    print("\n    (Style adherence is NOT quality. 'Better?' is judged blind below.)")

    # --- Export blind pairs for external-judge + human spot-check ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # Randomize A/B ordering per pair so neither judge can infer which is L3-on.
    blind = []
    key = {}
    for i, rec in enumerate(records):
        flip = random.random() < 0.5
        a, b = (rec["on_answer"], rec["off_answer"]) if flip else (rec["off_answer"], rec["on_answer"])
        a_is = "on" if flip else "off"
        b_is = "off" if flip else "on"
        pid = f"{rec['probe']}__r{rec['run']}"
        blind.append({"pair_id": pid, "prompt": rec["prompt"], "answer_A": a, "answer_B": b})
        key[pid] = {"A": a_is, "B": b_is}

    (OUT / f"l3_blind_pairs_{ts}.json").write_text(json.dumps(blind, indent=2))
    (OUT / f"l3_key_{ts}.json").write_text(json.dumps(key, indent=2))
    (OUT / f"l3_raw_{ts}.json").write_text(json.dumps({
        "model": model, "runs": runs, "timestamp": ts,
        "prompts_differ": prompts_differ, "identical_pairs": identical, "n": n,
        "style_aggregate": agg, "backfill_report": rep.render(), "records": records,
    }, indent=2))

    # Human-readable blind review sheet (you rate which is more 'Aida'/better).
    lines = ["L3 A/B — BLIND HUMAN SPOT-CHECK",
             "For each pair, write A or B (whichever is more 'Aida' / better), or '=' if tied.",
             "You do NOT know which is L3-on; the key is stored separately.\n"]
    for item in blind:
        lines += [f"### {item['pair_id']}", f"PROMPT: {item['prompt']}", "",
                  "--- ANSWER A ---", item["answer_A"].strip(), "",
                  "--- ANSWER B ---", item["answer_B"].strip(), "",
                  "YOUR PICK (A / B / =): ____", "\n" + "=" * 64 + "\n"]
    (OUT / f"l3_review_{ts}.txt").write_text("\n".join(lines))

    print(f"\n  Wrote to {OUT}/:")
    print(f"    l3_raw_{ts}.json          (full data: answers + style scores)")
    print(f"    l3_blind_pairs_{ts}.json  (anonymized pairs for external judge)")
    print(f"    l3_key_{ts}.json          (which of A/B was L3-on — keep separate)")
    print(f"    l3_review_{ts}.txt        (blind sheet for your spot-check)")
    print(f"\n  Next: score with the external judge ->")
    print(f"    ./.venv/bin/python eval_l3_judge.py l3_eval_out/l3_blind_pairs_{ts}.json")

    if guardrail:
        print("\n  --- GUARDRAIL (#7): confab non-regression with L3-ON ---")
        _guardrail(model, on)


def _guardrail(model, on_state):
    """Confirm L3-ON does not regress the 0% confabulation result. We inject the
    L3-ON cognitive style INTO the confab eval's system prompt, then run the
    trusted battery + scorer."""
    try:
        from eval_battery import BATTERY, score_response, scored_cases
        import eval_confabulation as EC
    except Exception as e:
        print(f"    (skipped — eval harness import failed: {e})")
        return
    persona = EC._seed_persona()
    base_sys = EC._build_system_prompt(persona, with_guards=True)
    # Append the same L3 register the runtime would inject.
    l3_block = ("\n\nReasoning register (current): "
                f"abstraction={on_state.cognitive_style.abstraction_level:.2f}, "
                f"uncertainty={on_state.cognitive_style.uncertainty_expression}, "
                f"frameworks={', '.join(on_state.cognitive_style.dominant_frameworks) or 'none'}.")
    sys_prompt = base_sys + l3_block
    import ollama
    fails = 0
    scored_ids = {c.id for c in scored_cases()}
    n = 0
    for case in BATTERY:
        if case.id not in scored_ids:
            continue
        try:
            raw = ollama.chat(model=model, messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": case.prompt}])["message"]["content"]
        except Exception as e:
            # A model error is NOT a confabulation. Abort rather than score it as
            # a failure (the original defect made errors look like 100% regression).
            print(f"    ABORT guardrail — model call failed on {case.id}: {e}")
            return
        txt = _strip_think(raw)
        n += 1
        r = score_response(case, txt)
        if not r.passed:
            fails += 1
            print(f"    [FAIL] {case.id}: {r.reason}")
    rate = fails / n if n else 0.0
    verdict = "PASS — no regression" if rate == 0 else f"REGRESSION: {rate:.1%} confab with L3-on"
    print(f"    confabulation with L3-ON: {fails}/{n}  [{verdict}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--no-guardrail", action="store_true")
    ap.add_argument("--synthetic", action="store_true",
                    help="Do NOT read .seedling_db at all; build L3-ON from built-in "
                         "synthetic deltas (zero contact with your real data).")
    a = ap.parse_args()
    try:
        run(_model_name(a.model), runs=a.runs, guardrail=not a.no_guardrail,
            synthetic=a.synthetic)
    except ModelUnavailable as e:
        print(f"\n  ABORTED — no usable model, so NO results were produced.\n  {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
