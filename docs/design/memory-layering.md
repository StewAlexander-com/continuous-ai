# Design: Layered Memory for Seedling

Status: **Phase 1 implemented; Phase 2 deferred**
Author: Stewart Alexander
Date: 2026-06-14

## Why

Seedling stored thread insights flat and, on restore, injected the single
`latest_delta()` as "most recent insight." We watched this fail live: a stale
session-1 tangent ("duplicate log messages...") kept reappearing every session,
crowding out durable facts like "I am Aida."

## Root cause (corrected after rubber-ducking)

The original hypothesis ("too many memories injected") was **wrong**. Restore
injects only ONE insight (`latest_delta`). The real bug is a **self-reseeding
loop**:

1. Restore injects the latest insight into context.
2. A small model (llama3.2:3b) parrots it in its reply.
3. Delta extraction captures that parroting as the new latest insight.
4. Repeat — the tangent reseeds itself every session.

Rubber-ducking also found: both the Aida delta and the tangent delta are
`emergent=True` with similar coherence, so any L1 "ranking" barely separates
them. What actually rescues Aida is **promoting user-stated facts to a stable
layer**, not re-ranking.

## Prior art (ideas borrowed, NOT code)

Inspired by concepts from two projects; **no code copied from either**:

- **Memory layering** — durable facts live in a separate, always-present layer;
  transient observations stay demoted. Concept inspired by
  *TencentDB-Agent-Memory* (no clear OSS license → concept only, nothing reused).
- **Promote-don't-overwrite / multi-signal recall** — accumulate additively;
  surface by fused signals, not one score. Concept inspired by *Mem0*
  (Apache-2.0).

Seedling adapts these to its own purpose — memory as reasoning state with a
built-in skeptic — with an independent implementation.

## Phase 1 (shipped) — the minimal, low-risk fix

Two changes, both validated by isolated simulation against the real Aida/tangent
history:

### 1. Kill the self-reseed (injection fix)
The "most recent insight" slot now prefers the latest **non-emergent** insight,
falling back to the latest only if all are emergent. Emergent-only tangents stop
reseeding themselves through the recent-insight slot.

### 2. Persona layer — user-stated facts only (promotion)
A new L2 `PersonaMemory` holds a small, capped set of **durable facts that trace
to an explicit user statement** (e.g. "remember...", "your name is...",
"I prefer..."). These are ALWAYS injected, so identity ("I am Aida") survives
regardless of L1 churn.

**Safety: promotion is gated on a real user utterance**, detected by a
deterministic heuristic over *this session's user turns* — not by asking the 3B
model to self-report a fact (which it could confabulate). The fact TEXT comes
from `insight_gained`; the GATE is "did the user actually say something
memory-like this session." No fuzzy/embedding dedup (see deferred Phase 2):
dedup is exact-normalized text only, so reinforcement is conservative and we
never risk merging distinct identity facts ("Aida" vs "Ada").

Persona is capped (default 12). On overflow, the lowest `reinforce_count`
(then oldest) is evicted, and every promotion/eviction is logged.

## Phase 2 (DEFERRED — do not build yet)

These carry real risk and need a stronger substrate (embeddings or a larger
model) to be safe:

- **Fuzzy/semantic dedup & reinforcement** — "my name is Aida" vs "the user
  named me Aida" are the same fact in different words. Prefix dedup is too weak;
  embedding dedup risks FALSE merges that corrupt identity. Deferred until
  reliable dedup is available.
- **Multi-signal top-K L1 ranking** — only worth it once there are many threads;
  today restore injects one insight, so this is premature.
- **Atom/persona age-out tuning, confabulation drill-down checks.**

## Schema (Phase 1)

```python
@dataclass
class PersonaFact:
    text: str                 # the durable fact (from insight_gained)
    kind: str                 # "identity" | "preference" | "constraint"
    source_thread_id: str     # provenance → drill-down to L0 transcript
    promoted_at: datetime
    reinforce_count: int = 1

@dataclass
class PersonaMemory:
    facts: list[PersonaFact] = field(default_factory=list)
```
`ContextState` gains `persona: PersonaMemory` (defaults empty → backward-compat
with old stored states).

## Backward compatibility

Old `ContextState` JSON blobs have no `persona` key → default empty PersonaMemory
applies on load. Existing stale deltas (incl. the emergent log-tangent) are
untouched; they simply never promote, and the non-emergent recent-slot fix stops
the tangent from reseeding going forward. No DB migration.

## Attribution (in code + README)

> Seedling's layered memory is an independent implementation inspired by ideas
> from Mem0 (Apache-2.0) and TencentDB-Agent-Memory. No code from either project
> is used — only the high-level concepts of memory layering and
> promote-don't-overwrite recall informed this design.
