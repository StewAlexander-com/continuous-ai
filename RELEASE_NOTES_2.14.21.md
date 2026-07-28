<!-- release-title: v2.14.21 — Stop attach-file prose from false-triggering memory correction -->
## In plain language

Asking a question about an attached file should get an answer about the file — not a “which stored fact is wrong?” menu. A JS comment like *“the correct radar is visible”* inside a widget was matching the correction detector because the whole file body was scanned.

**Bottom line:** `:read` Q&A stays on-topic; correction still works on your plain-language turn.

---

**TL;DR:** **v2.14.21** scopes live memory-correction parsing to the ask/question region of attach turns (same boundary as persona promotion), and treats `The user attached …` ask-suffix lines as attach pollution so they cannot become durable persona facts.

## Why this release matters

v2.14.20 hardened guards and local-only defaults. In real use, reviewing a Weebly weather widget with “Any obvious improvements?” opened the correction disambiguation UI: the file body contained `the correct … is`, which matches a legitimate correction trigger meant for turns like “the correct location is Mebane.” Fail-safe listing then dumped every persona fact — including old attach-framing noise — instead of answering the question.

## What's new / fixed in 2.14.21

- **Attach-aware correction scan** — `_handle_correction` runs `_parse_correction` on `_persona_scan_region(user_input)` only, so file prose cannot open the prune menu.
- **Ask-suffix pollution filter** — `_is_attach_pollution` also rejects lines starting with `The user attached ` (runtime framing, not a user fact).
- **Regression test** — `test_attach_body_does_not_trigger_correction` pins the weather-widget false positive.

## What did not change

Correction triggers, deterministic prune/replace, and fail-safe numbered disambiguation for *real* corrections are unchanged. No model or config swap required.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

If persona memory still lists old `The user attached …` rows from earlier sessions, clear them with `bash run.sh forget` (they are noise, not facts).

## Tests

- `test_correction.py` — attach-body false positive + ask-suffix pollution detector
- Existing correction / directive suites — green

**Full changes:** `v2.14.20..v2.14.21`
