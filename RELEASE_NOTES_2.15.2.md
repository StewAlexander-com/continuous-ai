<!-- release-title: v2.15.2 — Ask before searching a folder that is not on the list -->
## In plain language

If you name a folder in `:search` or `:scan` that is not on the allowlist, Aida asks `y/N` before going further. Yes writes the folder into `config.yaml` (comments kept) and runs the command. No leaves the list alone. A path that does not exist is not added.

`:allow` lists the folders, adds one (same y/N), or drops by number so a mistaken yes is easy to undo. Feature flags stay off until you edit them yourself.

**Bottom line:** a forgotten allowlist entry is a question, not a dead end.

---

**TL;DR:** **v2.15.2** lets you grow `rga_search_allowed_paths` from chat when you name a real path. Default is N. Live `config.yaml` is only written after you confirm.

## What's new / fixed in 2.15.2

- **Confirm-to-allow** — `:search <pattern> in <path>` and `:scan <path>` ask when the path exists and is outside the list, then persist and continue.
- **`:allow`** — list / `:allow <path>` / `:allow drop N`. Does not flip `rga_search_enabled` or `security_scan_enabled`.
- **Harness** — `test_rga_allow_harness.py` covers y, N, missing path, already-listed, drop, and `:scan` y. Never writes the live config file.

## What did not change

Search stays text-first. Scan findings are still not sent to the model. Readers are untouched.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Then: `:search foo in ~/Documents` — say `y` to add that folder. `:allow` to review or drop.

## Tests

- `python test_rga_allow_harness.py`
- `python test_rga_capability_harness.py`

**Full changes:** `v2.15.1..v2.15.2`
