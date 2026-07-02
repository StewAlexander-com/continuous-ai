**TL;DR:** Fixes a real workflow bug in file paging: attaching a file with `:read` (or paging with `:more`) no longer makes Aida answer *before* you can page the rest. She now **stages** what you page and only replies once you actually ask — so you can walk through a large file chunk-by-chunk and then ask one question about the whole thing.

### 🩹 Fixed
- **`:read` / `:more` answered too early.** Previously, `:read <file>` (with no trailing question) and every `:more` immediately sent that chunk to the model and streamed a reply — so you could never type `:more` to reach the next chunk. Now:
  - **`:read <path>`** (no question) shows chunk 1, **stages it, and waits** — hint: *':more' for the next part, or ask a question about it*.
  - **`:more`** pages the next chunk and **stages it** (no reply).
  - Your **next message** folds all staged chunks + your question into a single turn, and *then* Aida answers. An empty **Enter** with staged content means "respond now" (a quick orientation). Paging state persists, so `:more` keeps working after.

### ✅ Non-regressive
- **`:read <path> <question>`** still answers in one shot (unchanged).
- **CSV** still shows its structural summary; if you didn't include a question it now waits for one (consistent with `:read`).
- The clean user question (not the folded file text) still drives the collaborative wall; only the model turn carries the file content.

### 🧪 Tests
- New `test_read_staging.py` (6 tests) around a pure, deterministic turn-composer `_compose_staged_turn()`: plain turn passthrough, empty-input no-op, staged+question folding (order preserved, buffer consumed), empty-Enter orientation, partial-view note, and `None`-safety.
- Full offline suite **32/32 green**; `compileall` + `schemas.py` + `eval.py` clean.

**Full changes:** `v2.11.0..v2.11.1`
