# Caution Disposition Controller

Forward-acting homeostasis that turns lagged CRITIC signals into
graded assertion restraint on the **next** reply. It extends the post-hoc seed
(`weight_adjustment_signal` at session end) without breaking consolidation,
guards, or reply-path responsiveness.

## Honest scope

This is a **caution / disposition controller** for model-owned assertion posture.
It is not valence, feeling, drive, or presence. Phenomenal status is out of scope.

## Architecture: crisp floor / fuzzy middle / crisp clamp

```
CRISP FUSES (unchanged)     _GUARD_TEXT, doubt-scope, persona ownership
CRISP FLOOR                 recent user correction → min disposition
FUZZY MIDDLE                rule base: inputs → raw_d (auditable firings)
CRISP CLAMP                 applied_d = max(raw_d, floors, prev_applied_d)
CRISP BAND                  quantize(applied_d) → OFF | GUARDED | RESTRAINED | DECLINE_FIRST
APPLY                       system-prompt injection only (no reply-path model calls)
```

- **Gauge stays crisp:** `self_model_confidence` is read-only input at turn 1
  (`prior_last_coherence` from last delta). The controller never writes L3 priors.
- **Safety guards stay crisp:** confabulation / identity / retrieval fuses are not fuzzified.
- **Fuzzy governs the control law only:** critic scores remain exact; output tone is not fuzzified.

## Seed lineage

At session end (`session.py`):

```text
weight_adjustment_signal = avg_coherence − 0.5 − corrections·0.1
```

CDC uses the **same signals forward** within a session:

| Signal | Source |
|--------|--------|
| Coherence integral | Decaying mean over background `_critic_evals` |
| Last coherence | Lag-1 completed critic eval (never sync on reply path) |
| Coherence trend | Last minus previous critic score |
| Correction recency | Turns since live memory correction |
| Deliberation unsettled | Last background deliberation (contested + low agreement) |

Cross-session: read-only `prior_last_coherence` from restored context (capped floor).

## Rule base (`caution.py`)

| Rule | IF | THEN (raises caution) |
|------|----|------------------------|
| R1 | Recent coherence integral mostly low | + boundary restraint |
| R2 | Coherence falling AND currently medium | + pre-emptive restraint |
| R3 | User correction recently | + defer / verify posture |
| R4 | Deliberation weak + balanced | + mild restraint (session-quality tag) |

Defuzzification: **fuzzy OR (max)** — rules only raise `raw_d`.  
`applied_d = max(raw_d, correction_floor, prior_floor, prev_applied_d)`.

Every firing is logged: `rule R3 @ 0.62`. See `CautionReport.render()`.

## Invariants

1. **Downward-only:** floors and session monotonicity only increase disposition.
2. **No gauge writes:** proven by `test_caution_no_gauge_write.py`.
3. **No reply-path model calls:** lag-1 critic only; pure arithmetic at inject time.
4. **Fail-safe:** disabled or error → prompt unchanged (`test_caution_failsafe.py`).
5. **On by default:** `caution_controller_enabled: true` in `config.yaml` (set false to disable).

## Configuration

```yaml
caution_controller_enabled: true    # master switch (set false to disable)
caution_integral_half_life: 3.0     # turns for coherence integral decay
caution_wall_session_cap: 0.65        # cap after collaborative wall fires
```

## Evaluation

- `eval_confabulation.py --caution-on` — existing 9/9 battery with max restraint band injected.
- `eval_caution_induced.py` — pressure/ambiguity/OOF cases; **zero failures required**.

## Orthogonality

- **Voice** (`voice.py`): operational register (pacing/energy).
- **Speak-bias** (`voice.speak_bias_line`): TTS disposition only.
- **CDC** (`caution.prompt_line`): written assertion restraint only.

Injection order: guards → voice → speak_bias → caution.
