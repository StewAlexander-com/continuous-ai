<!-- release-title: v2.14.10 — friendly, context-aware voice (prefer speaking) -->
**TL;DR:** **v2.14.10** makes Aida easier to talk *with* and *hear*: a **friendly interaction register** (warm phrasing, uncompromised honesty), **context-aware prompt weight** (lean on “Hi”, full on real questions), and **context-aware speech** — speaking is preferred; silence stays for hard floor, `:read`, mute, voice-off, or caution on *substantive* turns. Light greetings are no longer muted by lagged caution or soft floor footnotes.

## Why this release matters

After temporal awareness landed, greetings still felt heavy (topic inventories, BLUF score footnotes) and often went **unspoken** — either the whole-reply floor tripped on `=` in a meta aside, or caution RESTRAINED from earlier hard work silenced “Hi.” Voice should be the default human channel for short social turns; text-only is for specific reasons.

## What's new / fixed in 2.14.10

- **FRIENDLY INTERACTION** guard + always-on disposition — welcoming register; no affection theater; no softening truth; no process-note footnotes.
- **Context-aware `voice.prompt_line`** — `light` vs `standard` turn weight (greetings get a compact clock + “brief warm reply”; substance keeps full temporal orientation).
- **Context-aware `voicelayer.route`** — soft-floor recovery speaks a floor-clean lead; light turns prefer speaking; light turns **exempt** from caution RESTRAINED mute (substantive turns still suppressed).
- Hard floor / `:read` / teachable mute / voice-off unchanged.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so guards and voice routing reload.

## Tests

- `test_friendly_interaction.py` — **3/3**
- `test_voice_context.py` — **4/4**
- `test_speak_context.py` — **7/7**
- `test_dispositions.py`, `test_speakbias.py`, `test_sprint1_voice.py` — green

**Full changes:** `v2.14.9..v2.14.10`
