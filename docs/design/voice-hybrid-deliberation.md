# Voice Layer — Design & Deliberation

**Status:** shipped. Voice is **ON by default** when macOS `say` is available;
turn it off in plain language ("go silent") or with `:voice off`. **Date:** 2026-06-27.
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
- **ON by default (when `say` exists); easy, discoverable off-switch.** Force
  off with `AIDA_VOICE=0` or `voice_enabled: false`. When off, the module is a
  complete no-op. The default flipped to on (2026-06-27) so a new user simply
  HEARS Aida — which makes off-switch discoverability a correctness requirement:
  the startup banner states it, and the first time she actually speaks she
  repeats how to silence her.
- **Offline:** macOS `say`; safe no-op on hosts without it.

## Controls
- **Say it in plain language (default path):** "go silent" / "be quiet" /
  "stop talking" / "mute" to silence; "speak again" / "voice on" / "unmute" to
  re-enable. Detected by `detect_voice_intent()` — deterministic, runs BEFORE
  the model, and CONSERVATIVE: only a whole-message imperative toggles, so
  discussing silence ('why did you go silent on X?') never mutes her.
- **Poka-yoke resume (impossible to get stuck silent):** while silenced, the
  PROMPT itself shows `[voice off — say "speak again" to resume]` every line, so
  the way back is always on screen. The resume vocabulary is wide ("you can talk
  now", "turn the voice back on", "speak up", "resume voice"…) so a natural
  attempt just works, and resuming SPEAKS a one-time "Voice is back on." so you
  get sensory confirmation it worked. Bare `:voice` prints status + controls.
- `:voice off` / `:voice on` — explicit command fallback.
- `:quiet` — stop speaking the KIND Aida last spoke (teachable mute).
- Force off at launch: `AIDA_VOICE=0` or `voice_enabled: false`.
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
