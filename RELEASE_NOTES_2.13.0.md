<!-- release-title: v2.13.0 — Pick your local brain, tune her voice, know her preferences -->
**TL;DR:** Aida stays fully local and offline — but is now **easier to run and configure**. **v2.13.0** adds optional **LM Studio / llama.cpp / vLLM** support (cloud URLs blocked), in-chat **`:help` / `:setup` / `:model`**, **dynamic voice** (`:voice chatty|terse|normal`), and **structural preferences** (`:dispositions`) so she can explain her policies without pretending to have feelings.

## Why this release matters

If you already run Aida on Ollama, **nothing breaks** — Ollama remains the default. This release is about **choice and clarity**:

- **Pick your inference backend** — Ollama (default) or any **local** OpenAI-compatible server (LM Studio, llama.cpp, vLLM). Cloud endpoints are intentionally blocked.
- **Fix setup mistakes faster** — `:setup` shows backend, model, and connection status with actionable fixes; startup warns if the server or model isn't ready.
- **Control how much she speaks** — `:voice chatty`, `:voice terse`, or `:voice normal`; Kokoro pre-warms in the background; speech starts sooner after streamed replies.
- **Understand what she "prefers"** — `:dispositions` lists her **structural** preferences (policy, not emotion): honesty rules, L3 frameworks, caution, speak-bias. She no longer has to deny having preferences outright.

## What's new in 2.13.0

### Local inference adapter (Ollama unchanged by default)

- **`inference_backend: ollama`** — same as before; zero config change required.
- **`inference_backend: openai_compat`** — point at LM Studio (`:1234`), llama.cpp (`:8080`), or vLLM (`:8000`) in `config.yaml`. Load the model in your server UI; `:model` lists and switches by id.
- **Local-only guard** — non-loopback URLs are rejected; no accidental cloud use.

### In-chat setup UX

| Command | What it does |
|---------|----------------|
| `:help` | Full command reference |
| `:setup` | Backend, model, connection status + fix steps |
| `:model` / `:models` | List and switch models (Ollama auto-pulls; openai_compat switches only) |
| `:dispositions` | Your structural preferences vs hers — policy, not emotion |

Startup runs a **best-effort preflight** (warns, never blocks chat).

### Voice dynamics (Sprint 1)

- **`:voice chatty|terse|normal`** — session-only control over how much she speaks aloud.
- **Caution-aware voice** — under RESTRAINED/DECLINE_FIRST caution, voice suppresses unless you're on `:voice chatty`.
- **Kokoro pre-warm** — neural voice loads in the background at startup when enabled.
- **Post-stream overlap** — TTS fires on the final reply immediately after printing (lead sentence first on long answers).

### Structural preferences

- Aida can articulate **policy preferences** (ranked dispositions from L3 + config) without claiming emotional "likes."
- Injected each session with pedagogy; guard text teaches the vocabulary.
- Distinguishes **your** preferences (persona memory) from **hers** (structural policies).

### Docs (no runtime change)

- Leaner README, site critique answers, refreshed hero/social assets for cross-platform messaging.

## Upgrade

```bash
cd continuous-ai   # or your clone path
git pull
bash setup.sh      # only if deps changed; safe to re-run
bash run.sh
```

**New config keys** (all optional; defaults preserve v2.12 behavior):

```yaml
inference_backend: "ollama"   # or "openai_compat"
# openai_compat_base_url: "http://127.0.0.1:1234/v1"   # LM Studio
```

Try in chat: `:help` · `:setup` · `:dispositions` · `:voice chatty`

## Tests

- Full offline suite **37/37 green** (adds llm adapter, inference UI, Sprint 1 voice, and dispositions coverage).
- Confabulation battery unchanged at **0%** with guards on.

**Full changes:** `v2.12.0..v2.13.0`
