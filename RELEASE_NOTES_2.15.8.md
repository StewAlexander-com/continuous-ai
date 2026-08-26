<!-- release-title: v2.15.8 — Four things the project claimed and did not do -->
## In plain language

Aida's pitch is that she won't pretend and that you own the truth. This release fixes four places where the code did not hold up that claim.

She would confirm a correction and then, one line later, repeat the fact you had just corrected. The secret scanner could not see `.env` — the single file it exists to check. After a `:scan`, asking "which of the above are false positives?" got a confident answer about something else entirely, because the scan output never reached her and she reached for the only text she could see: her own instructions. And turning the scanner on told you to edit a YAML file and restart, which was never actually necessary.

None of these were failures of the memory design. Three of the four are the same shape: a command handled locally in the CLI returns early, so nothing about it reaches the model, and nobody had checked what the model then does with a question about it. Silence about an action is not the same as honesty about it.

**Bottom line:** two honesty bugs fixed, one security-scan false negative fixed, and one piece of busywork removed.

---

**TL;DR:** **v2.15.8** stops a corrected fact from being re-asserted in the same session; makes `:scan` see hidden files, so a key in `.env` is actually found; makes a follow-up about `:scan` output get an honest "that wasn't shared with me" instead of a confabulation; and replaces "edit config.yaml and restart" with `:enable scan`. Plus a README that leads with mutualism and a hero image that stopped contradicting the headline under it.

## Before (v2.15.7)

**A landed correction did not survive the same turn.** Captured verbatim:

```
[memory: corrected — removed "Remember that I live in Mebane, North Carolina.";
 saved "Durham, North Carolina"]
You: Where do I live? One short sentence.
Aida: I recall: You live in Mebane, North Carolina.
```

A fresh session answered "Durham" correctly, which is what located the fault. Storage was fine. `chat()` returns early the moment `_handle_correction` handles a turn, so the correction never entered `self._messages`; the transcript kept the original statement, carried no trace of the fix, and the model preferred what it could actually read.

**The secret scanner could not see `.env`.** `run_scan` passed `--no-ignore` with the comment *"catch gitignored .env under the allowlist"*. `--no-ignore` defeats `.gitignore`; it does not reach hidden files, and ripgrep skips dotfiles unless asked. Verified: an `AKIA` key in `.env` was missed, while the same key in a visible file was found. Every true-positive test in the suite wrote `leak.txt`, which is exactly why this survived seven releases.

**A follow-up about scan output got a confabulation.** After a `:scan` printed 78 findings:

```
You: which of the above is false positives and what should I work on?
Aida: The context you're referencing isn't a list of claims to evaluate for
      false positives — it's a set of instructions governing how I operate as
      Aida. The "beliefs" listed (like your name being Stew Alexander)...
```

Findings are deliberately never sent to the model. But "by design" only ever covered the *findings* — that a scan had happened at all was equally invisible, so "the above" resolved against the only text in context: the injected system prompt. Not merely lost context; a confident answer to a question nobody asked.

**Opening a gate was busywork.** `:scan <path>` with the gate off printed *"Set security_scan_enabled: true in config.yaml … then restart."* The restart was fiction — both gates are read from the live config dict per command. And `_ensure_named_roots_allowed` already asked y/N and wrote `config.yaml` preserving comments, three lines away; it was just called with `offer=enabled`, so the prompt was only ever offered to people who had already turned the gate on.

## Now (v2.15.8)

- **Corrections reach the window** ([`d716a1d`](https://github.com/StewAlexander-com/continuous-ai/commit/d716a1d)) — `_record_supersession` marks the superseded turns and `_correction_inject` appends the current value to a **copy** of the system message, the same contract as `_voice_inject` / `_caution_inject`. It annotates and never deletes: dropping the earlier turn would make the record disagree with what you actually said, which is the same class of silent rewrite this project exists to prevent. Your words stay byte-for-byte and gain a marker. Deterministic containment over already-collected turns — no model call, nothing on the reply path.

- **`--hidden`** ([`e308917`](https://github.com/StewAlexander-com/continuous-ai/commit/e308917)) — one flag, and no companion excludes were needed: `rga_search._EXCLUDE_GLOBS` already drops `.git/`, `.venv/` and `node_modules/` from every text search, so hidden *files* are reached without walking a repo's object store, which is also what keeps the documented "no git history" scope true. Deliberately no further dotdir excludes: for a secret scanner a false negative costs more than noise.

- **An honest answer about output she never got** ([`7bed4df`](https://github.com/StewAlexander-com/continuous-ai/commit/7bed4df)) — `note_local_action` records *that* a local command ran, and `_handle_local_reference` short-circuits a turn that points at its output **before the model is consulted at all**: *"I can't see that. `:scan` printed its output in your terminal and its results are deliberately never sent to me… paste the lines you want triaged."* A prompt instruction was tried first and was not enough — llama3.2 ignored "you cannot see that output" and explained its own dispositions back. The answer to an unreliable model here is not a firmer prompt but deterministic code in front of it, as with correction pruning and doubt-scope.

- **`:enable scan`** ([`aa85b19`](https://github.com/StewAlexander-com/continuous-ai/commit/aa85b19)) — `:scan` and `:search` now offer the gate with a y/N and carry on in the same turn. New `flags.py` owns the boolean write path, line-surgical so the comments that explain this config survive. What may be toggled from chat is an explicit two-item allowlist, **not** "anything ending in `_enabled`": capability gates only widen how much disk Aida may read, while `caution_controller_enabled`, `deliberation_enabled` and `chain_of_verification_enabled` stay in `config.yaml` on purpose. Those are the reason confabulation measures ~0% instead of ~20%; a chat command able to switch them off would let one sentence disable the central claim, and would put them within reach of model suggestion. Widening a permission is a grant; narrowing an honesty guard is a regression, and it should cost a file edit and a restart.

- **Docs** ([`3093247`](https://github.com/StewAlexander-com/continuous-ai/commit/3093247), [`72a05c1`](https://github.com/StewAlexander-com/continuous-ai/commit/72a05c1)) — the README hero image was a generation behind, selling *"give your local LLM a memory"* directly above an H1 reading *"a truly honest local AI"*, with a terminal line that skipped `setup.sh` and so failed on a fresh clone. Regenerated from the site's own tokens and copy. The opening argument now leads with mutualism, the collaborative wall gets its own capability row, and the rare-pairing claim is calibrated to match what the site is willing to say in public — the site called it *"a comparative claim from review, not a formal survey"* while the README stated it flat, and docs being less calibrated than marketing is the worst kind of bug here.

## What did not change

Memory layering, belief deliberation, doubt-scope, the caution controller, the guards, and the confabulation behaviour. No database change, no schema change, no prompt-core change. `bash run.sh confab-eval` and `bash run.sh smoke` measure the same things they did in v2.15.7; a 17/17 smoke run on `llama3.2` is included in the verification below.

**Verified, not assumed:** live on `llama3.2`, after a correction, *"Where do I live?"* → *"Durham, North Carolina."* Live after a `:scan`, the reported question returns the refusal above, with the system prompt checked to contain no paths and no matched text. `test_security_scan.py` 7/9 → 9/9. `test_correction.py` 11 → 16 checks. New `test_flags.py` (8) and `test_local_actions.py` (13). Every new test fails on its parent commit. Regression sweep green across 23 suites.

**Known limits, named:**

- **`docs/assets/demo.gif` still shows the old memory-only story.** The recorder and tape were rebuilt around teach / correct / refuse-to-invent / restore, but the GIF was not re-recorded, so what ships is the previous capture.
- **The offline refusal is phrasing-sensitive, and this release does not fix it.** Same model, same guards, same URL: *"What does `<URL>` say about installation?"* refuses correctly; *"Summarize what `<URL>` says."* invented the page's contents. `eval_battery.py`'s URL case uses `https://example.com/some-post`, a placeholder with nothing to confabulate *from*, while a real slug-rich URL hands the model a project name and a topic. The scorer would catch it — this is a prompt-realism gap, not a scorer gap — but it means the headline 0% is measured on a battery less adversarial than an ordinary user.
- **A correction stores a value without its predicate.** `promote_persona_fact` receives the bare extracted string (`"Durham, North Carolina"`). Previously the model recovered the meaning from the raw transcript; now that the transcript is marked, the missing predicate is visible. The inject carries the old text alongside so the relationship is recoverable, but the stored fact is still contextless.
- **`_handle_local_reference` can fire on a genuine question.** After a scan, "false positives" in an unrelated sense will be short-circuited. It tells you exactly what happened, and a pasted block is exempt, but it is a deliberate false-positive trade.
- **`scan_summary_to_model` defaults to `false`**, so by default Aida cannot help triage a scan unless you paste the lines. The default reveals only that the command ran — not even whether anything was found — so *"nothing is sent to the model"* stays literally true. Counts and kinds are opt-in, and never paths or matched text.
- **`test_inputsafe.py` blocks forever on `input()` when run non-interactively**, so that suite cannot run unattended as written.
- **The README still mixes "it" and "she"** ("Teach **it** live" against "the runtime that powers **her**"), `Recent` still sits above the features, and the L3 A/B result is still inside a collapsed block.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

No migration, no database change. **Restart any running session** — Python loads `session.py` and `seedling.py` at import, so a session started before the pull keeps the old behaviour. Config *flags* apply live, which is the whole point of `:enable`; code does not.

`config.yaml` gains one key, `scan_summary_to_model: false`. Existing values are untouched.

**Full changes:** `v2.15.7..v2.15.8`
