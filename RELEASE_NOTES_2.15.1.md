<!-- release-title: v2.15.1 — Poke-yoke :search and :scan -->
## In plain language

`:search` and `:scan` now fail closed on the mistakes people actually make: no pattern, extra words on `:scan`, a leading dash that looks like a flag, a config typo that would hang, a missing folder, a wall of loopback IPs.

Bare `:search` / `:scan` print usage plus whether the flag is on and which folders are allowlisted. `:search foo in ~/notes` narrows if the suffix looks like a path. `:scan` findings still print only — they are never sent to the model.

**Bottom line:** easier to use; common edges harden cheaply; no tail-chasing.

---

**TL;DR:** **v2.15.1** is a poke-yoke patch on the 2.15.0 search/scan commands. Usage is discoverable. Inputs that used to fall through to chat or to `rg` as flags now get a usage line or a deny.

## What's new / fixed in 2.15.1

- **Usage on the empty command** — bare `:search` / `:scan` (and `--help`) print ON/off and the allowlist. `:scan please` is usage, not a chat turn.
- **`:search <pattern> in <path>`** — only when the suffix looks like a path (`/`, `~`, `./`). English *“in the logs”* stays a pattern. Quotes strip.
- **Leading `-` is a pattern** — passed after `--` so it is not an `rg` flag.
- **Config clamps** — hits ≤ 200, timeout 1–60s, filesize must look like `4M`. Missing allowlist folders are skipped (or denied if none exist). Duplicate `path:line` hits collapse. Patterns over 400 characters deny.
- **`:scan <path>`** — scoped to one allowlisted folder. Loopback IPs are not findings. Reports cap at 40 lines. Findings are never staged into the model.
- **Errors name the next step** — off / empty allowlist / outside-allowlist point at `config.yaml` and `:capabilities`.

## What did not change

Flags stay human-gated. Text-first search + keep-on-timeout from 2.15.0 is unchanged. Readers (`filereader` / `pdfreader` / `docxreader`) are untouched.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Then: `:search <pattern>` or `:search <pattern> in <allowlisted-path>`. `:scan` or `:scan <path>`.

## Tests

- `python test_rga_capability_harness.py`

**Full changes:** `v2.15.0..v2.15.1`
