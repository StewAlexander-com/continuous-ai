**TL;DR:** Aida now has her name on every reply, paste-friendly multi-line input that's hardened against hostile content, and a calmer console. Quality-of-life + safety, no breaking changes.

### ⌨️ Multi-line input (paste-friendly, hardened)
Paste code, configs, or logs freely — a **blank line sends**. stdin is treated as untrusted: terminal-hijack escape sequences, control bytes, and "trojan-source" Unicode (bidi/zero-width) are stripped; oversized pastes are truncated with a loud notice. Your real code/CSV survives **verbatim** (newlines, tabs, Unicode preserved). Commands (`:model`, `:read`, `exit`) stay single-line, so a pasted block can never switch models or quit.

### 🏷️ She has a name
Replies are now labeled **`Aida:`**, not `Model:` — the visual channel finally matches the prose. All console styling runs through one themed module that respects `NO_COLOR` and non-TTY output.

### 🔧 Fixes
- Deliberation drain timeout no longer fires on large models (qwen3:30b) — scales with in-flight work; a deferred belief promotion never loses the underlying insight.
- Python 3.11 f-string compatibility (CI now green on 3.11–3.13).

### 🔒 Tests
+74 new tests across input-hardening, the UI module, and consolidation. Full suite **19 suites green**, verified on Apple M1 Max.

**Full changes:** `v2.5.0..v2.6.0`
