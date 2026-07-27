<!-- release-title: v2.14.19 — Stop chat/critic VRAM thrash on 32GB Macs -->
## In plain language

On a 32GB Mac, Aida’s big chat model and her small grading model were **kicking each other out of memory** on every turn — about six seconds of reload before the next reply could even start. This patch makes them **share the machine peacefully**, and clears the waiting spinner as soon as she begins reasoning (not only when the first visible word appears).

**Bottom line:** same brain, same honesty — less dead air between turns.

---

**TL;DR:** **v2.14.19** caps chat context at 8k, keeps the critic at a 2k window with a long keep-alive, starts Ollama with `OLLAMA_MAX_LOADED_MODELS=2`, warms both models at session start, and clears the CLI spinner on the first thinking token.

## Why this release matters

v2.14.18 fixed *queue* contention (background work yielding to your turn). On Apple Silicon with ~25GB Metal “VRAM,” Ollama still defaulted chat to a **32k** context (~19–22GB), so loading `gemma3:4b` for grading **evicted** the chat model every time. Measured reload cost: ~6.5s before the next turn. Dual-load only sticks when chat is ~8k and the critic is ~2k.

## What's new / fixed in 2.14.19

- **`chat_options: { num_ctx: 8192 }`** — stops the 32k Metal auto-default; room for a coresident critic.
- **Critic `num_ctx: 2048` + `keep_alive: 30m`** — small, sticky grading footprint (`critic.py`).
- **`run.sh`** — exports `OLLAMA_MAX_LOADED_MODELS=2` (and `OLLAMA_KEEP_ALIVE=30m`) for daemons it starts.
- **Warmup** — preloads the pinned critic alongside chat so the first grade isn’t a cold load.
- **Streaming UX** — first thinking token clears the CLI spinner (thinking models emit reasoning before visible content).

## What did not change

Honesty gates, osmotic learning, foreground-priority scheduling, and the pinned `gemma3:4b` critic choice are unchanged. No model swap required.

## Upgrade

```bash
cd continuous-ai
git pull
# If Ollama was already running from before this patch, restart once so
# MAX_LOADED_MODELS=2 takes effect:
killall ollama
bash run.sh
```

Confirm coresidency (optional): after a turn, both `qwen3:…` and `gemma3:4b` should appear under `ollama ps`.

## Tests

- `test_responsiveness_tuning.py` — keep-alive / options expectations updated
- Full `test_*.py` suite — green
- Live check — both models coresident @ 8192 / 2048; spinner clear ~0.8s; 2nd chat without reload thrash

**Full changes:** `v2.14.18..v2.14.19`
