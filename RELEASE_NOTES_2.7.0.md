**TL;DR:** Aida can now **speak**. On macOS she reads her short replies aloud by default — and you can silence her just by saying "go silent." Fully offline, additive (text is still always the record), and built so secrets/code are never spoken.

### 🔊 Aida has a voice (on by default, macOS)
When `say` is available, Aida speaks her short, conversational replies aloud — a greeting, an acknowledgment. It's **additive**: the full reply is always printed and remains the record; speech is just a parallel rendering of a safe subset. Fully offline (macOS `say`, no cloud, no deps).

### 🤫 Turn it off (or on) by saying so
- Say **"go silent"** / "be quiet" / "stop talking" / "mute" to silence her.
- Say **"speak again"** / "voice on" / "unmute" to bring it back.
- `:voice off` / `:voice on` work too; `:quiet` mutes just the last kind she spoke.
The natural-language toggle is deterministic and conservative — only a whole-message command toggles, so *discussing* silence never accidentally mutes her.

### 🔒 Safe by design (the "don't tell secrets" floor)
A deterministic floor **never speaks** code, numbers, paths, URLs, key-shaped strings, shell commands, or anything from a `:read` file — and **errs to silence** on anything ambiguous. Only short, plain pleasantries are ever eligible. Every voice decision is logged in plain text so it stays auditable.

Force off at launch with `AIDA_VOICE=0` or `voice_enabled: false`. Non-macOS hosts stay text-only automatically.

### 🧪 Tests
+57 voice tests (floor safety, errs-to-silence, conservative toggle, no false-positives). Full suite **20 suites green**, verified on Apple M1 Max.

**Full changes:** `v2.6.1..v2.7.0`
