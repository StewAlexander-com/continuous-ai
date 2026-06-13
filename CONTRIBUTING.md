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

Use **Python 3.11–3.13** (3.14 lacks `lancedb`/`pyarrow` wheels).

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

Open an issue with: what you ran, what happened, what you expected, your macOS / Python / Ollama versions, and any relevant log lines from `logs/`.
