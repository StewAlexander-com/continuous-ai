# Voice Layer — Design & Deliberation

**Status:** shipped (opt-in, off by default). **Date:** 2026-06-27.
**Module:** `voicelayer.py` · **Tests:** `test_voicelayer.py` (34).

## What it is
An **additive** voice channel: when enabled, Aida ALSO speaks a safe, ephemeral
subset of her reply aloud (macOS `say`, fully offline). The full reply is always
printed and is always the record — voice never replaces text.

## The deliberation (how we got here)
This was reasoned through across several reframes (devil's-advocate first):

1. **Naive voice** (speak everything / replace text) — rejected. Kills
   auditability, breaks the paste/code workflow, drones dense reasoning, adds an
   untrusted STT input surface.
2. **Hybrid, model-chooses-modality** — better, but makes routing a fuzzy
   per-turn judgment and a new unaudited surface.
3. **Additive, ephemeral-only, text-always-the-record** — dissolves the
   auditability objection: voice duplicates a safe subset, never substitutes.
4. **Teachable routing** — the "toddler" insight: a mis-spoken pleasantry is
   low-cost and *correctable in plain text*, so misroutes are training events,
   not bugs. This is the project's native correction loop applied to delivery.
5. **The irreversibility floor** — the "don't tell secrets / don't swear"
   split: most routing is learned-and-correctable, but a small set of content
   must be a PRE-TAUGHT rule because the utterance itself is the harm and can't
   be un-said.

## The design (two control planes)

### 1. The floor (rules / oversight) — `floor_blocks()`
Deterministic, conservative, **not learnable**. Never spoken:
code fences, inline code, paths/URLs, long digit runs, key/token-shaped
strings, shell commands, config/code punctuation, and **anything while a file
is attached** (`:read` content). **Errs to silence**: any doubt → blocked. A
floor bug therefore over-suppresses (annoying), never over-speaks (harmful).

### 2. Teachable preference (learning / worth-it) — `route()` + `teach_mute()`
Above the floor, within the already-safe *ephemeral* set (short, ≤3 sentences,
no structure), Aida speaks pleasantries/acknowledgments/asides. Your `:quiet`
correction mutes the last-spoken KIND. **Learning only ever silences** — it can
never breach the floor.

## Invariants (honest by design)
- **Additive:** the full reply is always printed and logged; speech is a
  parallel rendering of a safe subset.
- **Audited:** every decision prints a dim note (`[voice: spoke greeting]`,
  `[voice: blocked by floor — code fence]`, `[voice: muted kind 'greeting']`).
- **Opt-in, OFF by default:** `AIDA_VOICE=1` or `voice_enabled: true`. When off,
  the module is a complete no-op (non-regressive by construction).
- **Offline:** macOS `say`; safe no-op on hosts without it.

## Controls
- Enable: `AIDA_VOICE=1` (env) or `voice_enabled: true` (config.yaml).
- `:quiet` — stop speaking the kind Aida last spoke (teachable mute).
- `:voice off` / `:voice on` — toggle mid-session.
- `AIDA_VOICE_NAME=<voice>` — pick a `say` voice.

## Honest scope & open questions
- **Worth-it is usage-gated.** At the desk you read faster than `say` talks; the
  value is real only in eyes-up / away-from-keyboard moments. The design is
  sound regardless; whether it's *used* is the user's call.
- **Floor is shape-based, not semantic.** It catches dangerous *forms*, not a
  secret phrased as plain prose — mitigated by the very narrow ephemeral scope
  and errs-to-silence default. Documented, not hidden.
- **Sequencing:** this is a second learned-ish layer; it sits BESIDE (doesn't
  touch) L3. The L3 blind quality eval remains the higher-integrity open item.
