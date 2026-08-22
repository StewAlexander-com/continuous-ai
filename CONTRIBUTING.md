# Contributing to Continuous-AI

Thanks for your interest. This is an experimental research runtime, so contributions of all sizes — bug reports, fixes, docs, and ideas — are welcome.

## Ground rules

- **Stay local-first.** The default path must work fully offline. Don't add required cloud dependencies; optional cloud features (like the Perplexity critic backend) must degrade gracefully when unavailable.
- **No stealth writes.** Any new state write should be logged, consistent with the runtime's auditability constraint.
- **Don't break schemas casually.** Changes to `schemas.py` affect stored state in LanceDB. Call them out explicitly in your PR.

## Development setup

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh
```

Use **Python 3.11–3.13** (LanceDB's 3.14 wheels are still inconsistent across platforms). The core runs on macOS, Linux, and Windows; on Windows, run the shell scripts under WSL or Git Bash.

## Before opening a PR

Please keep CI green. The same checks run locally:

```bash
python -m compileall -q *.py     # everything compiles
python schemas.py                # dataclasses instantiate + serialize
python eval.py                   # storage + evaluation + failure-mode suite
```

The failure-mode suite needs `lancedb`, `pyarrow`, and `pyyaml` but not Ollama, so it runs in CI without a model server.

## Pull requests

- Keep changes focused; one concern per PR.
- Describe what changed and why.
- If you touched a runtime path, say how you verified it (e.g. a real `chat` session, `status`, `eval`).

## Reporting bugs

Open an issue with: what you ran, what happened, what you expected, your OS / Python / Ollama versions, and any relevant log lines from `logs/`.


## Optional corpus search (`rga`)

`:search` / `:scan` shell out to an installed [ripgrep-all](https://github.com/phiresky/ripgrep-all) binary (`rga`). It is **not vendored**. The binary is AGPL; this repo stays MIT and only invokes it as a subprocess (mere aggregation). `pandoc` / `poppler` are likewise system tools if those adapters run.

Install yourself if you want the flags on:

```bash
brew install ripgrep-all poppler pandoc   # macOS
# or your distro's ripgrep-all / pdftotext / pandoc packages
```

Leave `rga_search_enabled` and `security_scan_enabled` **false** unless you have also set `rga_search_allowed_paths`. The `:capabilities` command cannot turn flags on.

In chat: `:search <pattern>` or `:search <pattern> in <path>`. A named path that is not on the allowlist asks `y/N` and, if yes, appends it to `rga_search_allowed_paths` in `config.yaml` (comments kept). `:allow` lists / adds / drops. `:scan` is read-only and is never staged into the model; `:scan <path>` narrows the same way. Bare `:search` / `:scan` print usage.

Capability tests:

```bash
python test_rga_capability_harness.py
```
