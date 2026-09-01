<!-- release-title: v2.15.11 — She can read what you named, without interrupting the chat -->
## In plain language

Three leaks into the wrong channel, all the same shape: work that belongs to the runtime was showing up as chat, or not happening at all.

Asking her to read a file mixed into a sentence (`just read :read ~/notes.md`) went to the model. She then correctly said she has no filesystem — and asked you to use `:read`, which you had already typed. The file never attached, so the conversation could not continue about it. That line now attaches the bytes **this turn**.

A background deliberation that hit the token cap logged `ERROR` onto the `You:` prompt. The fail-safe was doing its job (discard the fragment, keep the thesis). The severity was wrong: a designed miss was presented as a crash. It now stays in `logs/seedling.log` as INFO. Unexpected backend failures still ERROR.

She also had no honest way to *suggest* a command. Teaching the model the verb list and letting her dispatch would undo the reserved `:` channel and `:scan` privacy. This release adds the loop that does not: she may quietly offer `:read` / `:search` / `:more` when you already named a path; `y`/`ok` runs it; chatting expires the offer; `:scan` still never feeds findings.

**Bottom line:** a named local file can enter the chat, the prompt stays a prompt, and a command offer is optional chrome rather than an interruption.

---

**TL;DR:** **v2.15.11** attaches a named local file when you ask her to read it (including `just read :read ~/path`), keeps token-cap deliberation discards off the TTY, and lets Aida quietly offer `:read`/`:search`/`:more` — confirm with `ok`, keep talking to dismiss. She never runs commands herself.

## Before (v2.15.10)

`detect_local_read_intent` allowed `can you` / `please` before `read`, but not `just`, and did not look for an embedded `:read ~/path`. `just read :read ~/Desktop/x.md` fell through to `session.chat()`. Guards then told her to use `:read`.

A capped background synthesis that produced no usable verdict raised, and `deliberation.py` logged it at ERROR. The console handler is WARNING+, so the line landed on `You:`.

Colon commands were a reserved user channel. She had no catalog and no propose path, so she either lectured about `:read` or invented capability.

## Now (v2.15.11)

- **Conversational read is this turn** ([`efa7eb4`](https://github.com/StewAlexander-com/continuous-ai/commit/efa7eb4)) — `just read :read ~/x.md` / `just read ~/x.md` / `please :read ~/x.md` extract a path-shaped remainder, attach, and send the chunk now so she can talk about the file. `explain :read usage` stays chat. Bare `:read path` still stages and waits. `list ~/Documents` still opens the browse menu.

- **Token-cap discard is INFO** (same commit) — `_scrub_capped_output` raises `BackgroundCapMiss`. Deliberation logs that at INFO (file only). Unexpected failures stay ERROR on the TTY. The fragment is still discarded; the thesis is still kept.

- **Propose → confirm → runtime, conversation first** (same commit) — catalog injects only on a path/search turn. She may append `[offer :read <that path>]` (hidden while streaming). One dim line: `[offer :read ~/x.md]`. Confirm is whole-line `y`/`yes`/`ok`/`okay`. `sure` / `go ahead` stay chat. Unsolicited offers (no path in your last turn) are dropped silently. The next chat line expires a pending offer — you never have to say `n` to continue. `:scan`, `:forget-doc`, `:model`, `:enable`, `:q`, `:theme` cannot run from an offer.

## What did not change

Honesty guards, `GUARD_TEXT`, scan privacy (findings still never go to the model), the reserved `:` user channel, memory layering, deliberation fail-safes. No schema change.

**Verified:** `test_filereader.py` 50/50; `test_replcmds.py` 15/15; `test_command_offers.py` 4/4; `test_read_staging.py` 34/34; `test_deliberation.py` green; `test_gate_hardening.py` green; `test_inference_ui.py` 15/15.

**Known limits, named:**

- **She still does not browse the disk.** An offer must be grounded in a path you already named (or `:more` with an attachment). Guessing `~/Desktop/notes.md` during a philosophy chat is dropped.
- **`:scan` output stays in the terminal.** Confirming an offer never widens that. Paste lines if you want her to triage findings.
- **`ok` as a whole line runs a pending offer.** `ok, but first tell me about X` is chat and expires the offer.
- **Restart the session** to load `replcmds.py` / `filereader.py` / `seedling.py` / `session.py`.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

**Restart any running session.** Then `just read :read ~/path/to/file.md` should attach and answer in that turn; a quiet `[offer :read …]` is optional and never required.

No migration. No new config keys.

**Full changes:** `v2.15.10..v2.15.11`
