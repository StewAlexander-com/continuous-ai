<!-- release-title: v2.15.3 — :search understands English, files, names, and depth -->
## In plain language

`:search` used to treat whatever you typed as a ripgrep regex, dump the hits, and wait for you to ask a follow-up. It did not know a file from a folder, a name from contents, or an English question from a token.

Now it discriminates those things, interprets any English ask into needles (not a closed list of phrases), searches, then Aida reviews the hits with a path:line citation contract. A token is still exact. `"quoted"` is case-sensitive, then case-insensitive extras. `name <pat>` is file and folder names. `<what> /path/file.txt` is that file only.

**Bottom line:** say what you mean; name a file when you mean a file; Aida reads the hits instead of leaving a pile of `path:line` on the screen.

---

**TL;DR:** **v2.15.3** turns `:search` from “run this regex, stage the block” into “parse scope and kind, interpret English, search, review.” Allowlist / confirm-to-allow / `:scan` / flags are unchanged.

## Before (v2.15.2)

- `:search <pattern>` — the tail was a ripgrep regex. Quotes only stripped wrapping characters.
- `:search <pattern> in <path>` — optional folder (or file) root. Same regex, whole tree unless you named a file by luck.
- Hits staged like `:read`. Zero matches printed `no matching content found` and did **not** call the model. A hit list waited for you to type a question.
- No depth. No name-vs-content. No “this English sentence means retry/backoff.” `I'm looking for any loops` was searched as those words.
- Bare `:search /path/file.txt` treated the path string as the pattern.

## Now (v2.15.3)

- **Token** — unquoted identifier is exact text (`-F`).
- **Quoted** — `"AlphaToken"` is exact, case-sensitive first, then case-insensitive extras.
- **Names** — `:search name widget` (also `files named`, `folders named`) matches file and folder names, not only contents.
- **File vs folder vs depth** — `<what> /path/file.txt` or `in /path/file.txt` is that file only (siblings are not searched). `in <dir> depth 1|3|all` limits a tree (`1` = direct children; omit depth = all layers).
- **English** — any natural-language ask (`retry logic`, `where is the timeout`, `I'm looking for any loops`) is interpreted into needles via a **stateless** model call (`_chat_once`). Not `session.chat`, not the belief ledger. The interpreter cannot widen a file-only search into a tree, and cannot use the sentence itself or `.` / `.*` as a needle.
- **Review** — after hits, Aida reviews them in-chat. Every claim about a match must cite `path:line`. Follow-ups keep that contract.
- **Usage** — `:help` and bare `:search` teach the modes. A bare file or folder path without a query asks for something to look for instead of searching the path string. A missing named path says `Named path does not exist: …`.

## What did not change

Flags stay human-gated. Confirm-to-allow and `:allow` from 2.15.2 still apply. `:scan` is still read-only and never staged into the model. Text-first `rg` + keep-on-timeout from 2.15.0 is unchanged. Readers (`filereader` / `pdfreader` / `docxreader`) are untouched.

## Try this

Search must be on (`rga_search_enabled: true` and a non-empty `rga_search_allowed_paths`). Then in chat:

```text
:help
:search
:search SearchDenied
:search "quoted token you know exists"
:search name widget
:search UNIQUE_TOKEN /path/to/one/file.txt
:search retry logic
:search I'm looking for any loops
:search timeout in ~/your/dir depth 1
```

What you should see:

1. Bare `:search` / `:help` list token, quoted, name, file-only, depth, and English — not only `in <path>`.
2. `SearchDenied` is exact text (a token, not English). Hits (if any) are reviewed immediately.
3. `"quoted token…"` prefers the exact case, then adds case-insensitive extras.
4. `name widget` lists files and folders whose **names** contain widget, not every file that mentions the word.
5. `/path/to/one/file.txt` after the query does not include a sibling that has the same token.
6. `retry logic` / `I'm looking for any loops` print `[interpreting search…]`, then needles such as retry/backoff or for/while — not the sentence itself — then a review that cites `path:line`.
7. `depth 1` misses a nested file that `depth 3` (or omitted depth) finds.
8. `:search /path/to/one/file.txt` with no query asks for something to look for; it does not search for the path string.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Needs the same binaries as 2.15.0 (`rg`; optionally `rga` + poppler/pandoc for PDF/Office). Flags and allowlist are unchanged.

## Tests

- `python test_search_intent.py`
- `python test_search_modes_harness.py`
- `python test_rga_capability_harness.py`

**Full changes:** `v2.15.2..v2.15.3`
