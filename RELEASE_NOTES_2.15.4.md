<!-- release-title: v2.15.4 — :search enhances Aida; rg is the engine -->
## In plain language

`:search` is an Aida capability. She interprets what you asked, looks in the folders you allowlisted, then reviews the hits. `rg` is a system binary she shells out to. It is not the product. If that first part were missing, this would be a chat wrapper around a tool you can already install.

**Bottom line:** Aida understands the ask and tells you what the hits show. Ripgrep does not get a better UI.

---

**TL;DR:** **v2.15.4** locks the product path: interpret → search → review. Help, `:capabilities`, and the interpret prompt lead with Aida. Flags stay optional specificity. A harness assertion fails the build if that order is inverted.

## Before

**Through v2.15.2** `:search` ran your tail as a ripgrep pattern, staged the hit list, and waited for a follow-up. That was grep in the chat.

**v2.15.3** added English interpretation, file vs folder vs depth, names vs content, quoted exact, and an auto-review. The turn already enhanced Aida. The surface still taught flags first (`token = exact`, `name <pat>`, `depth 1|3`), which made it look like a ripgrep frontend.

## Now (v2.15.4)

Validated, not restated:

1. **Interpret** — English uses a stateless `_chat_once` (not `session.chat`, not the belief ledger). Meaning, not the sentence. Not a regex compiler.
2. **Search** — `rg` / `rga` stay subprocess-only. Never imported.
3. **Review** — with a session, Aida always reviews hits (`path:line` contract). Dump-and-wait is the no-session harness path only.
4. **Surface** — `:help` / bare `:search` / `:capabilities` lead with interpret → search → review. Quoted / name / file / depth are “or be specific.”

A product-lock test requires the first help line to say interprets before reviews, the interpreter to refuse “compiling a regex,” and the handler to call `_chat_once` then `_stream_turn`.

## What did not change

Allowlist, confirm-to-allow, `:scan`, flags, readers, text-first search + keep-on-timeout. `rg` is still something you install yourself.

This does not claim a live model will interpret every English phrase perfectly. It claims the runtime path is Aida’s, and the harness proves that path.

## Try this

Search on (`rga_search_enabled: true`, allowlist set). Then:

```text
:help
:search retry logic
:search I'm looking for any loops
:search SearchDenied
```

1. `:help` should say Aida interprets, searches, then reviews — before any flag list.
2. `retry logic` / `any loops` should print `[interpreting search…]`, then a review that cites `path:line`, not a leftover hit dump waiting for you to ask.
3. `SearchDenied` is a token (exact text), then still a review. That is Aida reading hits, not a better `rg`.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tests

- `python test_search_modes_harness.py` (includes the Aida-not-rg product lock)
- `python test_rga_capability_harness.py`

**Full changes:** `v2.15.3..v2.15.4`
