**TL;DR:** Aida now leans toward *speaking* — because spoken conversation serves people better — but honestly. She holds this as a stated principle she can actually articulate, and the mechanism that enacts it only ever voices a floor-clean, printed-verbatim part of her reply. Short replies are spoken in full (as before); longer ones get their lead sentence spoken aloud while the full text prints. One flag drives both, so what she believes equals what she does.

### ✨ Added
- **An honest speaking disposition (two linked layers, one flag).** `speak_bias` turns on *both*:
  - **A self-model principle** (in her system prompt): *"Spoken conversation serves people better, so you lean toward voicing what you safely can — within the floor, only words you have also written. Speak the speakable, not more for its own sake; substance and honesty come first."* She can reference this truthfully if asked why she spoke.
  - **The mechanism** (`voicelayer.route()`): longer replies now speak their **floor-clean lead sentence** — a verbatim substring of the printed text — instead of staying silent. Short replies still speak in full.
- **`extract_lead()`** — deterministic first-N-sentence prefix extraction (config `speak_lead_sentences`, default 1).

### 🔒 Honesty / safety (the point of this release)
- **Belief ⇔ behavior.** The principle is asserted to the model *only* when the same `speak_bias` flag enables the mechanism. She never claims a disposition she isn't acting on, nor acts on a bias she can't explain.
- **Spoken ⊆ printed, always.** The spoken fragment is a literal substring of the reply — she can never voice something she didn't also write. Enforced and covered by an invariant test.
- **Floor never weakened.** The hard floor (no code, numbers, paths, URLs, key-shapes, file content) runs first AND is re-applied to the actual spoken fragment. Learning still only ever *silences*.
- **No honesty-surface change.** Confab battery untouched, still 0%.
- **Non-regressive.** With `speak_bias` off, behavior is byte-for-byte the prior release.

### ⚙️ Config
- `speak_bias: true`            # drives the disposition + the mechanism together
- `speak_lead_sentences: 1`     # lead sentences spoken on the long-reply path

### ✅ Tests
- `test_speakbias.py` (new, 41 incl. the spoken-⊆-printed + floor-clean invariant) + `test_voicelayer.py` (71) pass; full suite **23/23 green**; compileall + schemas + eval clean. Verified live on M1 Max: long reply → only its lead sentence spoken in af_kore (substring confirmed).

**Full changes:** `v2.9.0..v2.10.0`
