**TL;DR:** Aida now has a **real neural voice** — Kokoro running fully on-device (no cloud, no server, no subscription). The clear `af_kore` female voice replaces the robotic macOS `say` for spoken replies, with `say` kept as an automatic fallback. Everything that decides *whether* she speaks is unchanged — this is presentation only.

### ✨ Added
- **Local neural TTS (Kokoro via `kokoro-onnx`, in-process).** Aida speaks with a natural neural voice (`af_kore`) generated entirely on your Mac. No network, no daemon, no API — consistent with the honest-aida thesis: offline and yours alone.
- **In-process, no server.** The model loads once as a lazy singleton at the first spoken turn and is cached; each utterance synthesizes to a temp wav and plays fire-and-forget via `afplay`, so it never blocks the printed reply. A failed load is remembered, so it never retry-stalls.
- **Automatic fallback.** If Kokoro or its model files aren't available at runtime, `speak()` transparently falls back to macOS `say` (which still honors `AIDA_VOICE_NAME`). It never raises into the reply loop.

### 🔒 Unchanged (additive only)
- The deterministic **floor** (never speaks code, URLs, paths, long numbers, key-shaped strings, or file-derived content), **ephemeral eligibility** (short conversational scraps only), and **teachable-mute** all run BEFORE `speak()` and are byte-for-byte behavior-preserved (covered by 71 voicelayer tests).
- **No honesty-surface change.** Confab battery untouched and still 0%.

### ⚙️ Config
- `tts_engine: "kokoro"`  (`"kokoro"` | `"say"`)
- `tts_voice: "af_kore"`  (Kokoro voice id, or a `say` voice when engine=say)
- `kokoro_model_path: "kokoro-v1.0.onnx"`, `kokoro_voices_path: "voices-v1.0.bin"`

### 📦 One-time local setup
```
pip install kokoro-onnx soundfile
curl -L -o kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voices-v1.0.bin   https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```
(If these aren't present, Aida simply uses `say` — nothing breaks.)

### ✅ Tests
- `test_kokoro_voice.py` (new, 15) + `test_voicelayer.py` (71) pass; full suite **22/22 green**; compileall + schemas + eval clean. Verified live on Apple Silicon (M1 Max): real-model synthesis dispatched and audible.

**Full changes:** `v2.8.0..v2.9.0`
