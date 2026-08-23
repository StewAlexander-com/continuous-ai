<!-- release-title: v2.15.6 — Run her from fish, zsh, or a double-click -->
## In plain language

Nothing about how Aida thinks changed in this release. What changed is getting her started. If you use **fish** or **zsh** rather than bash, the documented commands already worked — but `./run.sh` did not work for anyone, and on macOS the **Seedling.command** launcher could not be double-clicked from a fresh clone, which is exactly what the README tells you to do. Both are fixed. `setup.sh` also used to sign off by telling fish users to run a line that fish cannot parse; it now prints the line for the shell you actually launched it from.

**Bottom line:** `bash run.sh`, `./run.sh`, and double-clicking `Seedling.command` all work now, from whichever shell you prefer.

---

**TL;DR:** **v2.15.6** is a launcher and packaging fix. No Python changed. Three scripts gained the executable bit they were missing, `setup.sh` learned which shell it is talking to, and both launchers now stop instead of continuing if they cannot enter the project directory.

## Before (v2.15.5)

`bash run.sh` worked from any shell, but:

- `./run.sh` failed everywhere — `run.sh`, `setup.sh`, and `Seedling.command` were committed without the executable bit.
- `Seedling.command` could not be double-clicked on a fresh clone. Finder needs that bit before it will hand a `.command` file to Terminal.
- `setup.sh` ended by suggesting `source .venv/bin/activate`. In fish that is not a soft failure but a syntax error: the POSIX script dies on `_OLD_VIRTUAL_PATH="$PATH"` with *Unsupported use of '='*.
- Both launchers ran `cd "$(dirname "$0")"` unchecked. `run.sh` sets `-u`, not `-e`, so a failed `cd` would not have stopped it — it would have read config, written the database, and run tests against whatever directory you were standing in.

## Now (v2.15.6)

- **Executable** — all four shell entry points ship as mode `755`. `./run.sh` works, and `Seedling.command` opens on double-click.
- **Shell-aware setup** — `setup.sh` detects the shell that launched it and prints the matching activation line. It asks the parent process rather than `$SHELL`, which names your *login* shell and so is wrong for precisely the person trying a different one, and falls back to `$SHELL` when `ps` is unavailable.
- **Guarded `cd`** — both launchers exit with a message rather than running against the wrong directory.
- **Documented** — the README gains a table of the per-shell activate scripts and a note that `run.sh` calls the venv's Python by path, so activation is never required.

`bash run.sh` stays the headline instruction in the README rather than `./run.sh`, because it survives a checkout that lost the executable bit — Git Bash on Windows can do that, and Windows is a supported path.

| Shell | Activate, if you want it by hand |
|---|---|
| bash · zsh · sh | `source .venv/bin/activate` |
| fish | `source .venv/bin/activate.fish` |
| csh · tcsh | `source .venv/bin/activate.csh` |
| PowerShell | `.venv\Scripts\Activate.ps1` |

## What did not change

No Python was touched. Memory, beliefs, guards, `:search`, `:scan`, the allowlist, confirm-to-allow, readers, and every flag behave exactly as in v2.15.5. `rg` is still a subprocess you install yourself.

## Also in this window

The site at [honest-aida.ai](https://www.honest-aida.ai/) was reorganised so the quickstart leads and the long-form argument sits in accordions that open on demand, and the hero artwork was replaced. None of this is part of the installed runtime.

## Try this

From fish or zsh, in a fresh clone:

```text
./run.sh status
bash run.sh status
```

Both should behave identically. On macOS, double-click `Seedling.command` in Finder — first launch may still need right-click → **Open** once for Gatekeeper.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tests

Verified against zsh 5.9, fish 3.7.0, and tcsh, on a fresh clone: identical argv across bash, zsh, and fish for arguments containing spaces and colons; every printed activation line run in its own shell to confirm it activates. `bash -n` and `shellcheck -S warning` are clean on all four scripts, which also clears two pre-existing SC2164 warnings.

**Known issue:** the CI workflow has been red since well before this release (2026-08-08), across the whole 2.15.x line. Four checks fail for environment reasons rather than product ones — `rg` is not installed on the runners, a temp directory is created inside an allowlisted `/tmp`, and `_parse_read_arg` mis-splits a sample path that does not exist on the runner. This release does not address them.

**Full changes:** `v2.15.5..v2.15.6`
