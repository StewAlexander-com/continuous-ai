<!-- release-title: v2.15.5 — If the hits miss the ask, Aida may retry once -->
## In plain language

English `:search` already interprets, searches, and reviews. When the first needles are clearly the wrong kind of thing (e.g. `static` in a Python file when you asked for static global variables), Aida can notice, smoke-test one alternative on the **same** file or folder, and ask `Search '…' instead? [y/N]`. Yes runs a full search and she reviews that. No keeps the first result. Default is N. One retry, never a loop.

**Bottom line:** she may ask if the hits miss; she does not silently search twice or leave the file you named.

---

**TL;DR:** **v2.15.5** adds a one-shot fit gate on interpreted (English) searches only. Tokens and `"quoted"` skips it. Unsure → no nag. Smoke is cheap (8 hits, 4s). `rg` is still the engine.

## Before (v2.15.4)

Aida interpreted the ask, searched, and reviewed whatever came back. A bad interpretation still got a full review of the wrong hits. You had to run `:search` again yourself.

## Now (v2.15.5)

- **Fit check** — after an interpreted search, a stateless `_chat_once` (not `session.chat`, not the belief ledger) returns `fit` / `try`. Doubt or bad JSON means fit, so she does not nag.
- **Smoke** — only if fit is false and `try` is usable. Same roots, same file-only. If smoke finds nothing, she keeps the first search.
- **Ask** — `Search '…' instead? [y/N]`. N keeps the first hits and still reviews them. Y does one full search on the alternative, then one review.
- **Won’t** — widen a file-only search, echo the original sentence, retry tokens/quoted, or loop.

## What did not change

Allowlist, confirm-to-allow, `:scan`, flags, readers, interpret → search → review as the product path. `rg` is still a subprocess you install yourself.

## Try this

Search on. Name a real file that has no `static` but does have something you can retry toward:

```text
:search static global variables in /path/to/that/file.py
```

If the first needles miss and a quicker look finds something else, you should see a did-you-mean, then y/N. `y` reviews the retry in that file only. `N` reviews the first search. `:search SearchDenied` (a token) should not ask.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tests

- `python test_search_intent.py`
- `python test_search_modes_harness.py`
- `python test_rga_capability_harness.py`

**Full changes:** `v2.15.4..v2.15.5`
