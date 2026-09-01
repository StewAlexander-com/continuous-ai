<!-- release-title: v2.15.10 — Readable console, reserved command channel -->
## In plain language

Two things about talking to Aida in a terminal were leaking into the wrong channel.

The console had a name and a dim/yellow helper, but no actual appearance you could choose. If you wanted Monokai-like contrast you had to theme the terminal around her. This release adds three palettes — **b&w** (the default, type hierarchy only), **dark** (charcoal canvas, cream type, a cyan `Aida:` and orange warnings borrowed from Monokai, not a syntax port), and **light-color** (warm paper, same roles, darker accents). `:theme dark` applies immediately and is the next session's starting theme. `NO_COLOR` still wins.

Separately, a mistyped command was a chat turn. `:them dark` went to the model, who then guessed what you meant. A leading `:` on a single line is a control channel; sending it as prose lets her confabulate about commands she does not dispatch. Typos are now intercepted with a did-you-mean and are **not sent as chat**. A line that is a command with the `:` left off (`theme dark`, `help`, `search needle`) is offered as that command first; `n` sends the original text to Aida. English that merely starts with a verb (`help me understand`, `theme of the paper`) is left alone.

Also in the window since v2.15.9: `:scan myfolder/` used to print usage that listed that same folder as allowed. A relative scan path is a path by construction; it is no longer classified as prose.

**Bottom line:** the console is a choice, `:` is a channel, and a missed colon is a question rather than a guess.

---

**TL;DR:** **v2.15.10** adds `:theme dark|light-color|b&w` (default `b&w`, persists to `config.local.yaml`), stops mistyped `:commands` from reaching the model, and asks before treating a colon-less verb as a command. Also ships the already-landed `:scan myfolder/` path fix.

## Before (v2.15.9)

The visual channel was centralized (`ui.py`) but not choosable: dim chrome, yellow warnings, green `OK`. There was no canvas, no named palette, and no `:theme`.

Dispatch was an exact-match if-chain. `:them dark` and `theme dark` both fell through to `session.chat()`. `looks_like_command` existed but the chat loop did not call it, and it did not even list `:search` / `:scan` / `:enable` / `:quiet`. Awareness drifted from the if-chain.

`:scan stewalexander-com-git/` printed usage that then listed that folder under "Folders:" — refusing a path while advertising it as allowed. `parse_scan_arg` reused a search predicate that only accepts `/`, `~`, `./` and `../`, which is the right test inside `:search foo in the logs` and the wrong test when the whole argument is a path.

## Now (v2.15.10)

- **Console themes** ([`02f63ef`](https://github.com/StewAlexander-com/continuous-ai/commit/02f63ef)) — `theme: "b&w"` ships. `:theme dark` / `:theme light-color` / `:theme b&w` apply now and write `config.local.yaml`. Dark sets charcoal/cream via OSC 10/11 and restores on exit; speaker, warn, and OK are the only hues; reply body is not syntax-highlighted. Wrap math uses the visible `Aida:` width, so ANSI on the label cannot shift columns. `NO_COLOR` / non-TTY strip everything.

- **A reserved command channel** (same commit) — `replcmds.py` is the only verb list. After the if-chain, a leftover command-shaped `:verb` is intercepted: close typos get a did-you-mean (`:them dark` → `:theme dark`); far typos point at `:help`; nothing is auto-run. Smileys (`:)`, `:D`) stay chat. A line that looks like a command with the `:` left off is offered `[Y/n]` (default run it; `n` sends to Aida). `help me…` / `theme of…` / `search for…` are treated as English and not offered. Pipes skip the prompt.

- **`:scan myfolder/`** ([`54df201`](https://github.com/StewAlexander-com/continuous-ai/commit/54df201)) — scan arguments use a scan-specific path test, then resolve what you typed, `$HOME`-relative, or an allowlisted folder of that name. One match is scoped; two matches are listed for you to choose; zero matches say so. A scan never picks a folder for you.

## What did not change

Honesty guards, memory layering, deliberation, the confabulation battery, and `GUARD_TEXT`. No schema change. Color is a display choice, not a behaviour change: default `b&w` is type hierarchy (dim chrome, bold warn) instead of the old yellow/green hues.

**Verified:** `test_ui.py` 55 checks; `test_replcmds.py` 10 checks; `test_inference_ui.py` 14 checks; `test_inputsafe.py` 63 checks. `:theme` persists through a fresh `localconfig.load` the way a new session would. `:help` is consumed as a command; `theme dark` is not, until you confirm the missing-colon offer.

**Known limits, named:**

- **Default warn is no longer yellow.** `b&w` uses bold, not hue. That is the shipped default you asked for, not an accidental restyle of pipes (`NO_COLOR` was already plain).
- **OSC canvas needs a terminal that honours OSC 10/11.** If it does not, you still get coloured prefixes on whatever background you already had; we restore on exit either way.
- **The missing-colon prompt is interactive-only.** A piped `help` still goes to Aida, so scripts do not hang.
- **The site and demo GIF are unchanged.** Console appearance is not on [honest-aida.ai](https://honest-aida.ai).

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

**Restart any running session** — Python loads `ui.py` / `replcmds.py` / `seedling.py` at import. Then `:theme dark` if you want the charcoal canvas; last choice is kept.

No migration. `theme: "b&w"` is a new key in `config.yaml`; if you never `:theme`, nothing visible changes except warnings are bold rather than yellow. The first `:theme` writes `config.local.yaml` the same way `:enable` already does.

**Full changes:** `v2.15.9..v2.15.10`
