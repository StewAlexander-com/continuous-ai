<!-- release-title: v2.15.0 — Gated corpus search that stays fast on large trees -->
## In plain language

Aida can search folders you allowlist (`:search`) and optionally scan them for secret-shaped strings (`:scan`). `:capabilities` lists the flags. It cannot turn them on.

A first version of search died on a big Desktop tree: one `rga` process with PDF/zip adapters ran past 30s, then the timeout **threw away matches already printed**. Search is now text-first (ripgrep), streams JSON, keeps partial hits, and only then opens PDF/Office extractors. Archives are not walked on `:search`.

**Bottom line:** opt-in corpus search, path:line citations, large trees do not empty-timeout.

---

**TL;DR:** **v2.15.0** adds `:search`, `:scan`, and `:capabilities`. `rga` / `rg` are system binaries, never vendored. Flags stay human-gated. Text-first search + keep-on-timeout is the speed contract.

## Why this release matters

`:read` attaches one file you name. Searching a tree is a different capability. It is gated by `rga_search_enabled` and `rga_search_allowed_paths`. Wall-gate / caution / belief-deliberation are not filesystem permission systems.

## What's new / fixed in 2.15.0

- **`:search <pattern>`** — text-first `rg` (`--max-filesize`, media/archive globs), then `rga` with poppler+pandoc only if time remains. Hits stage like `:read` with a path:line citation contract. Zero matches print `no matching content found` and do not call the model.
- **Timeout keeps hits** — JSON is streamed; a hung extractor does not wipe matches already found. Message is honest (`partial: timed out…`) instead of a blank miss.
- **No zip/tar walk on `:search`** — Desktop/Downloads archives cannot stall the query.
- **`:scan`** — read-only secret/IP candidates via the same extraction path. Off by default. No git-history scan, no auto-fix, no live credential checks.
- **`:capabilities`** — read-only listing of gated flags and the exact enable path. One-time startup nudge; cannot flip flags.
- **Schemas** — `SearchHit` / `SearchResult` / `SecurityFinding` are ephemeral (not LanceDB rows).

## What did not change

`filereader.py`, `docxreader.py`, `pdfreader.py` and their tests are untouched. Belief deliberation is still only for model-derived insights — not for “should I mention :search?”

## Upgrade

```bash
cd continuous-ai
git pull
# search needs ripgrep; PDF/Office in :search needs ripgrep-all + poppler (+ pandoc)
#   brew install ripgrep ripgrep-all poppler pandoc
# then set in config.yaml:
#   rga_search_enabled: true
#   rga_search_allowed_paths: ["/your/notes", "/your/code"]
bash run.sh
```

In chat: `:search <pattern>` (single line), then ask about the hits.

## Tests

- `python test_rga_capability_harness.py`
- Timeout-keeps-hits + seedling+Desktop text query under 8s
- Existing CI eval suite

**Full changes:** `v2.14.21..v2.15.0`
