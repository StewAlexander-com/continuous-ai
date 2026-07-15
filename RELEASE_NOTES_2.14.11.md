<!-- release-title: v2.14.11 — smoke health: critic not starved by live delib -->
**TL;DR:** **v2.14.11** fixes a flaky install/health smoke check on large local models (e.g. `qwen3:30b-a3b`): background critic grading could time out because live deliberation held the same Ollama slot. Smoke now isolates that check and waits longer for the critic join.

## Why this release matters

`bash run.sh health` is the install/verification pipeline. On MoE/large hosts it reported UNHEALTHY even when Aida was fine — honesty stayed at **0% confab**, but smoke failed “critic eval lands after join” after a 60s wait while live deliberation still ran.

## What's fixed in 2.14.11

- **Smoke critic check** — `live_deliberation_enabled=False` for the streaming/grading session (sync deliberation at `end()` still exercises belief growth).
- **Longer critic join** — 180s (large models often need more than 60s for a second generate).

`setup.sh`, deps, parse gate, full unit suite, and honesty gate are unchanged.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh health
```

## Tests

- `smoke_test.py` — **17/17** on `qwen3:30b-a3b`
- Prior `run.sh health` static path: parse **84**, suites **45/45**, confab **0.0%**

**Full changes:** `v2.14.10..v2.14.11`
