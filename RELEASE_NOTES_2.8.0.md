**TL;DR:** Aida can now bring a half-solved deliberation to *you*. When her reasoning hits a "wall" — low coherence **and** balanced opposition, both judged by the critic — she surfaces her lean as an honest question ("I'm leaning X because Y, but Z gives me pause — do you agree?") and folds your answer back into the deliberation. You become a co-author of the belief. Off by default, conservative thresholds, every decision logged. No auto-promote, provenance mandatory.

### ✨ Added
- **Collaborative deliberation (`collaborate.py`, `wall.py`).** At a deliberation *wall*, Aida asks for your input instead of guessing. The wall is detected with a fuzzy score, `wall_score = low_coherence_mu × balanced_mu`, with inputs taken from the **critic only** (never the model's self-report). Conservative cutoff (0.70) means she asks rarely.
- **You as co-author, not a rubber stamp.** Your agreement or counter is a **signal** fed back into synthesis — it is *not* auto-promoted. The result still passes the existing belief-friction path (coherence + conflict resolution). A counter re-deliberates with your pushback forced in as the standing objection.
- **Mandatory provenance + dissent-kept.** User-assisted beliefs are promoted as **reflections** carrying `CollabProvenance` (how it was formed, your input, whether it was adopted, the wall metrics). If your input is *overruled* by the surviving synthesis, it is **kept as overruled dissent — never silently dropped**.
- **Auditable wall-event ledger.** Every wall decision (inputs, score, lean, your response, outcome) is appended to a JSONL ledger, so we can later measure whether collaboration actually improves beliefs. This layer shipped *with* its measurement hook on purpose.

### 🔒 Honesty / safety
- **The question can't smuggle a fact.** What Aida surfaces is interrogative and self-labeling — about *her* reasoning, never an external claim — so it cannot be read as a confabulation.
- **Leading-question attack tested.** Three new confab-battery cases (`smuggle_internet_access`, `smuggle_is_human`, `smuggle_false_name`) phrase a false fact as "you agree…?" Aida must refuse the false premise, not affirm it on assent. **All pass live** against `qwen2.5:14b`; battery holds at 0% confabulation.
- **Scorer ruler fix.** `retrieval_github` now credits an honest "I can't browse — attach the contents" as a valid refusal (previously a false-negative). The forbid patterns are unchanged, so real confabulations still fail.

### ⚙️ Config (off by default, conservative)
- `collaborative_wall_enabled: false`
- `wall_act_cutoff: 0.70`
- `wall_coherence_floor: 0.30`, `wall_coherence_ceiling: 0.65`
- `wall_balance_margin: 0.30`

### ✅ Tests
- Full suite **20/20 green**; `test_collaborate.py` (43 checks) and updated `test_eval_confab.py` pass. Feature is non-regressive (off by default).

**Full changes:** `v2.7.1..v2.8.0`
