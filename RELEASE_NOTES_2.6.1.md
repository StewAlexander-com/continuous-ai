**TL;DR:** Hotfix for v2.6.0 — a single-line message now sends on one Enter. The multi-line change made every turn wait for a blank line, so normal input appeared to hang on the first reply. Fixed. Please upgrade from v2.6.0.

### 🐞 Fixed
- **Input hang on first reply (v2.6.0 regression).** `read_multiline` required a blank line to submit *every* turn, so a normal single-line message waited for a second Enter — looking like a freeze. Now: a single typed line sends immediately on Enter; pasted multi-line blocks are still captured as one turn (drained from the input buffer, no blank line needed). All v2.6.0 input hardening (escape/control/Unicode stripping, size caps) is unchanged.

**Full changes:** `v2.6.0..v2.6.1`
