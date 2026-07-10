<!-- release-title: v2.14.3 — fix false “truncated reply” log line -->
**TL;DR:** **v2.14.3** fixes a confusing console line that made Aida look cut off mid-reply. When she used an `[EMERGENT]` marker, a **WARNING** with a 100-character preview printed on stderr right after her answer — easy to mistake for a truncated response.

## Why this release matters

After v2.14.2’s path-parsing fixes, some users saw output like:

```
Aida: BLUF: The resume is strong but could be optimized…
2026-07-10 … WARNING session: EMERGENT marker in response: … response_preview=BLUF: The resume is strong… infrastr
```

The full reply had already streamed. The WARNING was an audit preview capped at 100 characters, not a failed generation.

## What's fixed in 2.14.3

- **`[EMERGENT]` audit log demoted to INFO** — stays in `logs/seedling.log`, no longer bleeds into the chat console (console shows WARNING+ only).
- **Preview removed** — logs `chars=<length>` instead of a truncated copy of the reply, so it cannot mimic a cut-off answer.

`[EMERGENT]` itself is unchanged — still an honest model flag for unexpected observations.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tip: large `:read` glob + question

A one-shot `read …/*.pdf any insights?` still attaches **chunk 1** only. Use `:more` to page through files, or raise `num_ctx` in `chat_options` for longer replies.

**Full changes:** `v2.14.2..v2.14.3`
