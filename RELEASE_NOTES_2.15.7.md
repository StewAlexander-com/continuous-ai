<!-- release-title: v2.15.7 — Green CI, and it finally tests the search engine -->
## In plain language

Nothing about how Aida thinks changed in this release. What changed is that the automated checks are trustworthy again. CI had been red since 2026-08-08 — before the whole 2.15.x line — for four reasons that were all about the machines running the tests, not about Aida. That meant six releases shipped with no usable signal: nobody could tell a new regression from the standing noise. All four are fixed, and the Linux checks now install `rg` and exercise the real `:search` path for the first time.

One of the four turned out to be a genuine (small) bug in `:read`, which the test had been reporting honestly all along.

**Bottom line:** the checks are green, and green now means something.

---

**TL;DR:** **v2.15.7** clears the four standing CI failures. `rg` is installed on the Linux runner so `:search` is actually tested; three tests that need `rg` gained the project's existing `[SKIP]` guard; one allowlist fixture stopped depending on where `mkdtemp` lives; and `_parse_read_arg` now splits a named-but-absent file from a trailing question instead of swallowing part of the sentence into the path.

## Before (v2.15.6)

`python test_rga_capability_harness.py` ended `HARNESS FAILED (4/8 scripts green)`, and the `File menu` job failed on all three OSes. Five test names, four causes:

- **`rg` absent on the runners.** `rga_search.rg_binary()` is `shutil.which("rg")`, and the runtime deliberately does not vendor ripgrep. Around fifteen tests already carry `if not rs.rg_binary(): print("[SKIP] …"); return`; three did not — `test_name_search_files_and_folders`, `test_handler_english_reviews_interpreted_hits`, and `test_interpret_fail_is_honest`. The latter two drive `_handle_search_command` end to end, so with no engine they returned zero hits and failed with an empty assertion message, which is why the log looked blank next to those names. The workflow installed the storage stack but never the search engine, so Linux CI had **never** exercised the headline feature of 2.15.x.
- **A fixture that seeded an ancestor of its own temp dir.** `test_allowlist_yaml_add_drop_preserves_comments` wrote `rga_search_allowed_paths: [/tmp]` and then added `<mkdtemp>/notes`. On Linux `mkdtemp()` returns `/tmp/rga_yaml_*`, so the add was **correctly** refused as already-covered; on macOS it returns `/var/folders/…`, so it succeeded. The product was right on both platforms — the fixture was asserting a platform-specific temp-dir layout.
- **A parser fallback that assumed the sample file exists.** `_parse_plain_read_tail` resolves unquoted spaced paths by longest-existing-prefix. On a machine where `~/seedling/voice.py` exists it splits cleanly; where it does not, control fell to the whole-tail branch — which exists so an unmounted `"/Volumes/Backup Drive/2026 notes"` keeps its spaces in the error message — and returned `~/seedling/voice.py this is` as the path.

## Now (v2.15.7)

- **CI installs the engine** — the `Smoke tests` job installs `ripgrep` and echoes `rg --version` into the log. `:search` is tested on Linux for the first time. The `File menu` job is untouched; `test_filereader.py` never needed `rg`.
- **Skip guards, matching the existing idiom** — the three rg-dependent tests now print `[SKIP]` on a machine without ripgrep instead of failing. A contributor who has not installed it sees a skip, not a red X.
- **Hermetic allowlist fixture** — it seeds a sibling *inside* its own temp tree and asserts on that exact path, rather than seeding `/tmp` and asserting on a `/tmp` substring that would pass by accident.
- **`:read` splits a named-but-absent file from its question** — one additive branch in `filereader.py`, *below* the longest-existing-prefix loop, so a path that really exists still wins on disk evidence. A first token that already names a file (real 1–7-character alphanumeric extension; dotfiles excluded) followed by prose is a path plus a question. If any later token contains a separator, the space is inside the path and the old whole-tail branch still owns it.

That last one is a real fix, not just a test fix. `:read ~/notes/report.pdf what changed here` on a moved or mistyped file used to feed `~/notes/report.pdf what changed` into the did-you-mean ranker, so the candidates were scored against a polluted string.

## What did not change

Memory, beliefs, guards, the confabulation behaviour, `:scan`, the allowlist and confirm-to-allow, the readers, and every flag behave exactly as in v2.15.6. `rg` is still a subprocess you install yourself — CI installing it changes nothing about the runtime, which still refuses to vendor it. No default was flipped and no config key was added.

## Tests

Reproduced first, then verified — with `rg` present and with `rg` removed from `PATH`, on Linux/Python 3.12 and again on macOS (M1 Max, Python 3.12.13):

- `test_rga_capability_harness.py` → **`HARNESS PASSED (8/8 scripts green)`**, from `HARNESS FAILED (4/8)`.
- `test_rga_search.py` 18/18 · `test_search_intent.py` 15/15 · `test_search_modes_harness.py` 19/19 · `test_filereader.py` 49/49 · `test_rga_allow_harness.py` 7/7 · `test_read_staging.py` 29/0.
- With `rg` renamed away, the same scripts stay green — the guards degrade to skips.
- `eval.py`, the step that never got to run behind the red harness, passes: evaluation report plus 5/5 failure-mode suite.
- **Parser drift check:** 14 probe inputs, old versus new. Exactly one output changed — the target case. The other 13 are byte-identical, including unmounted spaced directories, spaced absent files, dotfiles (`~/.zshrc what does this set`), globs, quoted paths, and prose that merely mentions a filename.
- **Install smoke test:** fresh `--depth 1` clone into a clean 3.12 venv — `pip install -r requirements.txt` exit 0, `compileall` clean, `schemas.py` clean, and all thirteen runtime modules import with zero failures.

## Correction — there was a fifth cause, on Windows only

The four failures reported against v2.15.6 undercounted. The `File menu (windows-latest)` job was red on a fifth, unrelated cause, hidden behind the parse failure in the same job: `test_literal_path_with_star_wins_over_glob` opens a file named `foo*bar.txt`, and `*` is a reserved character in Win32 filenames, so the fixture dies with `OSError 22` before reaching its assertion. Windows was 47/49 on v2.15.6 and 48/49 once the parse split was fixed here.

It is fixed in [`72936be`](https://github.com/StewAlexander-com/continuous-ai/commit/72936be), which landed on `main` **after** this tag. The behaviour it checks — an existing literal name is not glob-expanded — is unreachable on Windows by construction, since no such name can exist there, so it skips with the reason named rather than pretending to cover it.

So: this tag is green on the three `Smoke tests` jobs and on the Linux and macOS `File menu` jobs, and still red on Windows. `main` at `72936be` is green on all six. Cloning the tag rather than `main` will show you that one Windows failure, and nothing else.

**Known limits, named:** `~/notes.d/My Folder` — an extension-shaped first token that is really a directory — would now truncate; it needs a nonexistent path, a suffix-shaped first segment, and a later space. `apt-get install ripgrep` pins nothing, so `rg --version` is logged to record what ran. Two `[SKIP] sample path not on this machine` checks in `test_filereader.py` remain permanently skipped on every runner: honest, but coverage the project believes it has and does not.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

No migration, no database change. If you had a local checkout with red checks, they should be green after the pull — install ripgrep (`brew install ripgrep` / `apt install ripgrep`) if you want the search tests to run rather than skip.

**Full changes:** `v2.15.6..v2.15.7`
