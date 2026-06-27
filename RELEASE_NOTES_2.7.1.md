**TL;DR:** Follow-up to v2.7.0 voice — once you've silenced Aida, getting her to speak again is now impossible to miss. A persistent on-screen hint, a wider "speak again" vocabulary, and a spoken confirmation when voice returns.

### 🔁 Can't get stuck silent (poka-yoke resume)
- **Always-visible hint while muted:** the prompt shows `You: [voice off — say "speak again" to resume]` on every line, so the way back is never lost to scroll.
- **Say it however feels natural:** "you can talk now," "turn the voice back on," "start speaking," "talk to me," "speak up," "resume voice," and more all resume — still whole-message and conservative, so *discussing* silence never toggles by accident.
- **Spoken confirmation:** when voice comes back, Aida says "Voice is back on." — sensory proof it worked.
- **`:voice`** (bare) prints current status and how to change it.

### 🧪 Tests
+14 voice tests (intuitive resume phrases, prompt-hint visibility, no false-positives). Full suite **20 suites green**, verified on Apple M1 Max.

**Full changes:** `v2.7.0..v2.7.1`
