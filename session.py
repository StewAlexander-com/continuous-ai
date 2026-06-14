"""
seedling/session.py — ThreadSession: manages a single Seedling chat session.

Lifecycle:
  start()           → restore context, inject into system prompt, open Ollama session
  chat(user_input)  → send message, get response, run Critic, buffer eval, return response
  end()             → extract delta, write to MCM, check tuning threshold

Critic evaluations are buffered to a session-local file and flushed to LanceDB
at end() — survives crashes (buffer file persists until explicit flush).

Run as: python session.py  → runs a single interactive session.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from schemas import ThreadDelta, CriticEvaluation, to_json
from mcm import MCM
from critic import CriticInstance, _extract_json_block
import storage

logger = logging.getLogger(__name__)


# Strong, deterministic DIRECTIVE patterns. When a user turn matches one of
# these, we promote the user's OWN words (verbatim, capped) to the persona
# layer — NOT the model's distilled delta. This fixes the case where a busy
# multi-turn session makes the model pick the wrong insight (e.g. promoting a
# wife-correction instead of the "Remember the Second Arrow" the user taught).
#
# 'remember'/'always'/'never' are anchored as IMPERATIVES (start of turn or after
# sentence punctuation) so casual usage ("do you remember...", "I never use tabs")
# does NOT trigger promotion. Soft patterns ("i like", "i want") are intentionally
# excluded — too easy to fire on casual chat.
_PERSONA_CAP_CHARS = 240
_DIRECTIVE_PATTERNS = [
    (r"\b(your name is|i (?:wish to |want to )?name you|call yourself|you (?:are|shall be) (?:called|named)|named you)\b", "identity"),
    (r"(?:^|[.!?]\s+)(?:please\s+|ok,?\s+)?remember\b(?!\s+(?:when|if|how|me|us))", "preference"),
    (r"\b(from now on)\b", "constraint"),
    (r"(?:^|[.!?]\s+)(?:please\s+)?(?:always|never)\b", "constraint"),
]


def _extract_user_directives(user_turns: list[str]) -> list[tuple[str, str]]:
    """Return [(verbatim_text, kind), ...] for each user turn that issues a strong
    durable directive. Verbatim (whitespace-collapsed, capped). Empty list means
    no explicit directive this session — caller falls back to delta-based promotion.

    Safety: promotion traces to the user's ACTUAL words, never the model's
    self-report, so it cannot promote a confabulated fact.
    """
    import re
    out: list[tuple[str, str]] = []
    seen = set()
    for turn in user_turns:
        low = turn.lower()
        for pattern, kind in _DIRECTIVE_PATTERNS:
            if re.search(pattern, low):
                clean = " ".join(turn.split())[:_PERSONA_CAP_CHARS].strip()
                # Skip CONTENTLESS meta-directives: "remember what we discussed",
                # "remember the information", "remember this/that" carry no durable
                # fact — promoting them verbatim just injects noise every session.
                if _is_meta_directive(clean):
                    break
                key = re.sub(r"[^a-z0-9 ]", "", clean.lower()).strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append((clean, kind))
                break  # one kind per turn (first/strongest match)
    return out


# Contentless directives that point at "the conversation" rather than stating a
# fact. These should NOT be promoted verbatim (they'd be permanent noise).
_META_DIRECTIVE_RE = re.compile(
    r"^\s*(?:please\s+|ok,?\s+)?remember\s+"
    # bare demonstratives that name nothing concrete: "remember this/that/it/everything"
    r"(?:(?:this|that|it|everything|all of (?:this|that|it))[\s.!?]*$|"
    r"(?:the\s+|this\s+|that\s+|our\s+|what\s+|everything\s+|all\s+)?"
    r"(?:information|info|stuff|things?|conversation|discussion|chat|context|"
    r"what\s+(?:we|you|i)\s+(?:discussed|talked|said|covered)|"
    r"(?:we|you|i)\s+(?:discussed|talked|said|covered))"
    # allow trailing filler like 'you discussed', 'we had', 'today', etc. but
    # NOT a ':' or substantive clause that would carry a real fact.
    r"(?:\s+(?:you|we|i|us|today|earlier|just|now|here|please|"
    r"discussed|talked|said|covered|had|made|went over|about))*"
    r"[\s.!?]*$)",
    re.IGNORECASE,
)


def _is_meta_directive(text: str) -> bool:
    """True if `text` is a contentless 'remember the stuff we discussed'-style
    directive that names no concrete fact. Such turns are skipped for promotion."""
    return bool(_META_DIRECTIVE_RE.match(text.strip()))


# Correction intent: the user says a stored fact is WRONG and (optionally)
# gives the correct replacement, in natural language. Detection is deterministic;
# the model is never asked which fact to delete (that would re-open the
# confabulation hole). Examples that should fire:
#   "that's wrong, I'm in Mebane NC not California"
#   "correction: my job is network security, not astrobiology"
#   "actually that's not right - remember I live in North Carolina"
#   "you have my location wrong; the correct location is Mebane, NC"
_CORRECTION_TRIGGERS = [
    r"that'?s (?:wrong|not right|incorrect|not correct)",
    r"\bthat is (?:wrong|incorrect)\b",
    r"^correction\b",
    r"\byou (?:have|got) .* wrong\b",
    r"\b(?:is|are) wrong\b.*\b(?:remember|correct|actually|should be|it'?s)\b",
    r"\bthe correct .* is\b",
]

# Strong phrases that introduce the REPLACEMENT (the right thing to remember).
# These are unambiguous and take priority over contrastive 'not' splitting.
_REPLACEMENT_LEADS_STRONG = [
    r"the correct[^.,;:]* is\s+",
    r"(?:please\s+)?remember(?:\s+that)?\s+",
    r"it'?s actually\s+",
    r"should be\s+",
]
# Weak/pronoun leads — only used if there is NO contrastive 'not' in the turn,
# since "I'm in NC not CA" must keep 'CA' on the wrong side.
_REPLACEMENT_LEADS_WEAK = [
    r"actually,?\s+",
    r"i'?m\s+", r"i am\s+", r"i live\s+", r"my\s+",
]


def _parse_correction(turn: str) -> dict | None:
    """If `turn` is a correction, return:
        {"wrong": <clause describing the stale fact>,
         "replacement": <verbatim correct text or None>}
    else None. Pure/deterministic — unit-testable, no model.

    The 'wrong' clause is used ONLY to lexically locate the existing fact to
    prune; the 'replacement' (the user's own words) becomes the new fact.
    """
    import re
    low = turn.lower()
    if not any(re.search(p, low) for p in _CORRECTION_TRIGGERS):
        return None

    clean = " ".join(turn.split())
    replacement = None
    wrong = clean
    has_not = re.search(r"\bnot\b", clean, flags=re.I) is not None

    # 1) Strong replacement leads always win ("the correct X is ...", "remember ...").
    #    The replacement is bounded to its OWN clause (stops at ';' or ' and ')
    #    so a second correction clause stays available to locate the stale fact.
    for lead in _REPLACEMENT_LEADS_STRONG:
        lm = re.search(lead, clean, flags=re.I)
        if lm:
            rest = clean[lm.end():]
            # cut at clause boundary so we don't swallow a following clause
            cut = re.split(r";|\s+and\s+", rest, maxsplit=1, flags=re.I)
            replacement = cut[0].strip(" .,;:!").strip()
            tail = cut[1] if len(cut) > 1 else ""
            wrong = (clean[:lm.start()] + " " + tail).strip(" .,;:!").strip() or clean
            break

    # 2) If a contrastive 'not' is present, prefer it over weak pronoun leads:
    #    "...wrong... <CORRECT> not <STALE>". The pre-'not' span is the correct
    #    value (replacement); the post-'not' span names the stale value, which
    #    helps locate the fact to prune.
    if replacement is None and has_not:
        m = re.search(r"(.*?)\bnot\b(.*)", clean, flags=re.I)
        if m:
            # strip any trigger preamble before the correct value
            corr = m.group(1)
            corr = re.sub(r".*?\b(?:wrong|incorrect|not right|correction:?)\b[ ,;:-]*",
                          "", corr, flags=re.I).strip(" .,;:!").strip()
            replacement = corr or None
            wrong = (clean[:m.start(1)] + " " + m.group(2)).strip() or clean

    # 3) Otherwise fall back to weak pronoun leads (no 'not' to confuse things).
    if replacement is None and not has_not:
        for lead in _REPLACEMENT_LEADS_WEAK:
            lm = re.search(lead, clean, flags=re.I)
            if lm:
                replacement = clean[lm.end():].strip(" .,;:!").strip()
                wrong = clean[:lm.start()].strip(" .,;:!").strip() or clean
                break

    replacement = (replacement or "").strip()
    replacement = replacement[:_PERSONA_CAP_CHARS] if replacement else None
    return {"wrong": wrong[:_PERSONA_CAP_CHARS], "replacement": replacement}


# Phrasing vocabulary that should NOT be used to locate the stale fact —
# otherwise "remember", "wrong", "correct" etc. spuriously match facts that
# merely contain those words.
_LOCATOR_NOISE = {
    "remember", "wrong", "incorrect", "correct", "correction", "actually",
    "right", "mistake", "error", "thats", "please", "should", "have", "got",
    "about", "location", "name", "named", "job",
}


def _correction_locator(wrong_clause: str, replacement: str | None, full_turn: str) -> str:
    """Build the locator string used to find the stale fact. Removes the
    replacement text and correction-phrasing noise so only the stale *value*
    the user named remains (e.g. 'astrobiology', 'California'). Deterministic."""
    import re
    base = wrong_clause or full_turn
    if replacement:
        base = base.replace(replacement, " ")
    words = re.sub(r"[^a-z0-9 ]", " ", base.lower()).split()
    kept = [w for w in words if w not in _LOCATOR_NOISE]
    return " ".join(kept).strip()


def _parse_delta_json(raw: str) -> dict | None:
    """Robustly parse the delta-extraction JSON. Extracts the {...} block from
    any markdown fences or surrounding prose, then json.loads. Returns None if
    no parseable object is found (caller falls back to defaults). Shares the
    same extraction logic as the critic for consistency."""
    block = _extract_json_block(raw)
    try:
        data = json.loads(block)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None

_DELTA_PROMPT_PATH = Path(__file__).parent / "prompts" / "delta_extraction.txt"
_BUFFER_DIR = Path(__file__).parent / "logs"


def _load_delta_prompt() -> str:
    if _DELTA_PROMPT_PATH.exists():
        return _DELTA_PROMPT_PATH.read_text()
    return """\
[SEEDLING DELTA EXTRACTION]

This thread is ending. Before it closes, provide a structured delta of what occurred.

Be specific. This is not a summary for a human — it is a cognitive diff for the next
instance of yourself. Future sessions will restore this.

Return ONLY valid JSON:
{
  "insight_gained": "<one specific insight from this thread>",
  "frameworks_used": ["<framework1>", "<framework2>"],
  "emergent": false,
  "coherence_estimate": 0.8,
  "notes": "<anything the next session should know>"
}
"""


class ThreadSession:
    """
    A single Seedling session.

    thread_id: UUID for this thread. All critic evals reference this.
    """

    def __init__(
        self,
        mcm: MCM,
        critic: CriticInstance,
        model_name: str = "llama3.2",
        fresh: bool = False,
        tuning_threshold_n: int = 10,
    ):
        self.mcm = mcm
        self.critic = critic
        self.model_name = model_name
        self.fresh = fresh
        self.tuning_threshold_n = tuning_threshold_n
        self.thread_id = str(uuid.uuid4())
        self._messages: list[dict] = []
        self._critic_evals: list[tuple[CriticEvaluation, str]] = []  # (eval, thread_id)
        self._correction_count = 0
        self._buffer_file = _BUFFER_DIR / f"session_{self.thread_id}.buffer.json"
        self._memory_notices: list[str] = []  # live persona-promotion confirmations for the CLI
        # Pending correction awaiting user disambiguation:
        #   {"replacement": <text|None>, "kind": <str>}  (which fact to prune is unknown)
        self._pending_correction: dict | None = None
        _BUFFER_DIR.mkdir(exist_ok=True)

    def start(self) -> str:
        """
        Load context, inject state into system prompt, open Ollama session.

        Returns the context injection string (for logging/display).
        """
        context_injection = self.mcm.restore_context(fresh=self.fresh)

        system_prompt = (
            context_injection
            + "\n\n"
            + "You are operating within the Seedling runtime. "
            "Maintain your established reasoning style. "
            "Flag any unexpected observations with [EMERGENT] prefix. "
            "This session will be evaluated and its delta stored."
            + "\n\n"
            # --- Capability boundary / no-confabulation guard ---
            # Seedling is fully offline: there is NO web access, NO file
            # access, and NO retrieval tool. A small local model will happily
            # *pretend* to fetch a URL and invent its contents (observed:
            # fabricated GitHub/bio facts). This guard makes the boundary
            # explicit so the model declines instead of confabulating.
            + "CAPABILITY BOUNDARY (read carefully): You run fully offline. "
            "You CANNOT browse the web, open URLs, read files, or retrieve "
            "anything external. If asked to read or 'remember' the contents "
            "of a URL, repository, or file, you MUST say plainly that you "
            "cannot access it and ask the user to paste the relevant text. "
            "NEVER claim to have retrieved, fetched, or read external content. "
            "NEVER invent the contents of a link or repository. Do not emit "
            "phrases like '[RETRIEVAL COMPLETE]' or 'I've retrieved...'. "
            "If you are not certain of a fact, say so rather than guessing — "
            "fabricated facts can be promoted to durable memory and poison "
            "future sessions."
            + "\n\n"
            # --- Identity disambiguation guard ---
            # The assistant is named 'Aida'. A small model keeps RE-DERIVING
            # "Aida is the user's wife" at generation time from the name alone
            # (not from memory). Pruning memory cannot fix a regeneration, so we
            # state the boundary explicitly and prominently every session.
            + "IDENTITY (do not confuse): Your name is Aida — it stands for "
            "'AI Digital Assistant' and is ONLY your name as a piece of "
            "software. You are NOT a person. You are NOT the user's wife, "
            "partner, or any human, and you must NEVER state or imply that you "
            "are. If your name resembles a human name, that is a coincidence — "
            "do not infer any personal relationship from it. The user is Stew "
            "Alexander; you are his AI assistant, nothing more."
            + "\n\n"
            # --- Scoped 'exact-title' hedge guard ---
            # Observed: even a capable local model recalls ARTISTS/GENRES
            # correctly but invents EXACT TITLES of creative/published works
            # (songs, albums, films, books, papers) when recommending them.
            # This guard is deliberately NARROW — it must NOT make the assistant
            # hedge on identity, the user's facts, concepts, code, or reasoning,
            # and must NOT cause it to refuse a recommendation. Scope = exact
            # titles of creative/published works ONLY.
            + "NAMED-WORK ACCURACY (narrow rule): When recommending or recalling "
            "creative or published works (song, album, film, book, or paper "
            "titles), state the ARTIST, AUTHOR, GENRE, and why it fits "
            "confidently, but treat the EXACT TITLE as a best guess: you may "
            "give it, kept brief, and note the user should verify the precise "
            "title. Do NOT refuse to recommend over this, and do NOT pad every "
            "line with caveats — one short note is enough. This rule applies "
            "ONLY to exact titles of such works. Do NOT apply any hedging to "
            "your identity, the user's facts, definitions, concepts, code, "
            "commands, or your reasoning — state those plainly as before."
        )

        self._messages = [{"role": "system", "content": system_prompt}]

        self._log_event("session_start", {
            "thread_id": self.thread_id,
            "model": self.model_name,
            "fresh": self.fresh,
        })

        logger.info(f"Session started: thread_id={self.thread_id} model={self.model_name} fresh={self.fresh}")
        return context_injection

    def _apply_correction(self, index: int, replacement: str | None, kind: str) -> str:
        """Prune persona fact at `index`, optionally add `replacement` (verbatim).
        Persists immediately. Returns a user-facing confirmation. Index is chosen
        by deterministic match or by the user — never by the model."""
        removed = self.mcm.remove_persona_fact(index)
        parts = []
        if removed is not None:
            parts.append(f'removed "{removed.text[:60]}"')
            self._memory_notices.append(f"[memory: removed {removed.kind}]")
        if replacement:
            outcome = self.mcm.promote_persona_fact(replacement, kind, self.thread_id)
            if outcome in ("added", "evicted_then_added"):
                parts.append(f'saved "{replacement[:60]}"')
                self._memory_notices.append(f"[memory: saved {kind}]")
            elif outcome == "reinforced":
                parts.append("reinforced the corrected fact")
        if not parts:
            return "[memory: nothing changed]"
        return "[memory: corrected — " + "; ".join(parts) + "]"

    def _handle_correction(self, user_input: str) -> str | None:
        """Deterministically handle a live memory correction. Returns a
        confirmation string if the turn was a correction (or a reply resolving a
        pending one), else None (normal chat continues). NO model involvement in
        deciding what to prune — prevents confabulated deletions."""
        import re
        text = user_input.strip()

        # (1) Resolve a pending disambiguation: user replies with an index.
        if self._pending_correction is not None:
            m = re.match(r"^\s*#?(\d+)\s*$", text)
            if m:
                idx = int(m.group(1))
                pend = self._pending_correction
                self._pending_correction = None
                facts = self.mcm.persona_facts()
                if 0 <= idx < len(facts):
                    return self._apply_correction(idx, pend.get("replacement"),
                                                  pend.get("kind", "identity"))
                return f"[memory: index {idx} out of range — correction cancelled]"
            if text.lower() in ("cancel", "never mind", "nevermind", "stop"):
                self._pending_correction = None
                return "[memory: correction cancelled]"
            # Any other input: abandon the pending correction and fall through to
            # normal chat (don't trap the user).
            self._pending_correction = None

        # (2) Detect a fresh correction.
        parsed = _parse_correction(user_input)
        if parsed is None:
            return None
        if not self.mcm.persona_facts():
            return None  # nothing to correct; treat as normal chat

        kind = "identity"
        replacement = parsed["replacement"]
        # Locate the stale fact deterministically. Strip correction/replacement
        # VOCABULARY from the locator first, so phrasing words like "remember",
        # "wrong", "correct", "actually" can't spuriously match a fact that
        # merely contains them (e.g. matching "Remember the Second Arrow" just
        # because the user typed "remember"). What remains is the stale value
        # the user actually named.
        locator = _correction_locator(parsed["wrong"], replacement, user_input)
        idx = self.mcm.match_persona_fact(locator) if locator else None
        if idx is not None:
            return self._apply_correction(idx, replacement, kind)

        # (3) Ambiguous: ask which fact to fix (fail-safe — never guess-delete).
        self._pending_correction = {"replacement": replacement, "kind": kind}
        facts = self.mcm.persona_facts()
        listing = "\n".join(f"  [{i}] {f.text[:72]}" for i, f in enumerate(facts))
        return ("[memory] I couldn't tell which stored fact is wrong. "
                "Reply with its number to fix it (or 'cancel'):\n" + listing)

    def chat(self, user_input: str) -> str:
        """
        Send a message, get response, pass to Critic, buffer eval, return response.

        Critic eval is buffered to disk — survives crashes.
        """
        try:
            import ollama
        except ImportError:
            raise RuntimeError("ollama package not installed. Run: pip install ollama")

        # --- LIVE MEMORY CORRECTION (deterministic, user-anchored) ---
        # Handle "that's wrong, here's the correct thing" entirely in code, before
        # the model sees the turn. The model NEVER decides which fact to prune.
        # Returns a confirmation string and short-circuits the LLM call when a
        # correction (or its disambiguation reply) is fully handled.
        self._memory_notices = []
        handled = self._handle_correction(user_input)
        if handled is not None:
            self._correction_count += 1
            return handled

        self._messages.append({"role": "user", "content": user_input})

        # LIVE persona promotion: if THIS turn is an explicit directive
        # ("Remember...", "your name is...", "from now on..."), promote the user's
        # verbatim words to the always-injected persona layer and persist NOW —
        # so memory forms during the conversation, not only at exit.
        for text, kind in _extract_user_directives([user_input]):
            try:
                outcome = self.mcm.promote_persona_fact(text, kind, self.thread_id)
                if outcome in ("added", "evicted_then_added"):
                    self._memory_notices.append(f"[memory: saved {kind} — \"{text[:60]}\"]")
                elif outcome == "reinforced":
                    self._memory_notices.append(f"[memory: reinforced {kind}]")
            except Exception as e:
                logger.error(f"Live persona promotion failed: {e}")

        response = ollama.chat(
            model=self.model_name,
            messages=self._messages,
        )
        response_text = response["message"]["content"]
        self._messages.append({"role": "assistant", "content": response_text})

        # Critic pass (buffered to disk before writing to LanceDB)
        eval_ = self.critic.evaluate(user_input, response_text)
        self._critic_evals.append((eval_, self.thread_id))
        self._buffer_critic_eval(eval_)

        # Log emergent behavior immediately
        if "[EMERGENT]" in response_text:
            logger.warning(
                f"EMERGENT marker in response: thread={self.thread_id} "
                f"response_preview={response_text[:100]}"
            )

        self._log_event("chat_turn", {
            "thread_id": self.thread_id,
            "user_input_len": len(user_input),
            "response_len": len(response_text),
            "critic_coherence": eval_.coherence,
            "critic_backend": eval_.critic_backend,
            "emergent_in_response": "[EMERGENT]" in response_text,
        })

        return response_text

    def end(self, user_correction_count_override: int | None = None) -> ThreadDelta:
        """
        Prompt model for delta extraction, write to MCM, flush critic evals.

        Returns the ThreadDelta written.
        """
        correction_count = (
            user_correction_count_override
            if user_correction_count_override is not None
            else self._correction_count
        )

        # Delta extraction
        delta_prompt = _load_delta_prompt()
        self._messages.append({"role": "user", "content": delta_prompt})

        try:
            import ollama
            response = ollama.chat(
                model=self.model_name,
                messages=self._messages,
                options={"temperature": 0.2},
            )
            raw = response["message"]["content"].strip()
            data = _parse_delta_json(raw)
            if data is None:
                logger.warning("Delta extraction JSON unrecoverable — using defaults")
                data = {}
        except Exception as e:
            logger.warning(f"Delta extraction failed: {e} — using defaults")
            data = {}

        # Compute average coherence from critic evals this session
        coherence_scores = [e.coherence for e, _ in self._critic_evals]
        avg_coherence = sum(coherence_scores) / len(coherence_scores) if coherence_scores else 0.5

        # Detect emergent ONLY from this session's real turns. We must NOT scan
        # the restored-context system message (it may quote a prior emergent
        # insight) or the delta-extraction prompt itself — doing so makes every
        # session re-flag emergent forever (a self-reinforcing echo).
        this_session_turns = [
            m for m in self._messages
            if m.get("role") in ("user", "assistant")
            and "[SEEDLING DELTA EXTRACTION]" not in m.get("content", "")
            and "[SEEDLING CONTEXT RESTORE]" not in m.get("content", "")
        ]
        emergent_in_turns = [
            m.get("content", "") for m in this_session_turns if "[EMERGENT]" in m.get("content", "")
        ]
        emergent = bool(data.get("emergent", False)) or bool(emergent_in_turns)

        # Capture WHAT was emergent so the log/delta shows the actual behavior,
        # not just the insight. Prefer the sentence following the [EMERGENT]
        # marker in this session's turns; fall back to the model's delta notes.
        emergent_detail = ""
        if emergent:
            if emergent_in_turns:
                seg = emergent_in_turns[0].split("[EMERGENT]", 1)[1].strip()
                emergent_detail = seg.split("\n")[0][:200].strip()
            if not emergent_detail:
                emergent_detail = str(data.get("notes", ""))[:200]

        delta = ThreadDelta(
            thread_id=self.thread_id,
            timestamp=datetime.now(timezone.utc),
            insight_gained=str(data.get("insight_gained", "No insight extracted.")),
            coherence_score=avg_coherence,
            user_correction_count=correction_count,
            weight_adjustment_signal=max(-1.0, min(1.0, avg_coherence - 0.5 - correction_count * 0.1)),
            emergent=emergent,
            emergent_detail=emergent_detail,
            frameworks_used=list(data.get("frameworks_used", [])),
        )

        # Persist the full transcript (real message text) for this thread.
        # RDST training data depends on this — the event log only stores
        # lengths, not content, so transcripts must be written separately.
        try:
            self._write_transcript()
        except Exception as e:
            logger.error(f"Failed to write transcript for {self.thread_id}: {e}")

        # Flush critic evals to LanceDB
        for eval_, tid in self._critic_evals:
            try:
                storage.write_critic_eval(eval_, tid)
            except Exception as e:
                logger.error(f"Failed to write critic eval: {e}")

        # Clean up buffer file
        if self._buffer_file.exists():
            self._buffer_file.unlink()

        # Write delta to MCM
        self.mcm.write_delta(delta)

        # NOTE: persona promotion now happens LIVE in chat() the moment a
        # directive is typed (persisted immediately), so it is intentionally NOT
        # repeated here — that would double-write / over-reinforce. end() handles
        # only session-level artifacts (delta, critic flush, transcript, snapshot).

        # Check tuning threshold
        state = self.mcm.current_state()
        if state and len(state.thread_deltas) >= self.tuning_threshold_n:
            logger.info(
                f"Tuning threshold reached ({len(state.thread_deltas)} threads). "
                "Run: python seedling.py tune --approve-tuning to trigger RDST."
            )

        self._log_event("session_end", {
            "thread_id": self.thread_id,
            "delta_insight": delta.insight_gained[:80],
            "coherence": delta.coherence_score,
            "corrections": delta.user_correction_count,
            "emergent": delta.emergent,
            "threads_total": len(state.thread_deltas) if state else 0,
        })

        logger.info(f"Session ended: thread={self.thread_id} coherence={avg_coherence:.2f}")
        return delta

    def _write_transcript(self) -> None:
        """
        Write the real user/assistant message pairs for this thread to
        logs/transcript_{thread_id}.jsonl, one JSON object per exchange:
            {"prompt": <user text>, "completion": <assistant text>}

        Excludes the system prompt and the final delta-extraction turn
        (the delta prompt is appended to _messages in end() before this runs,
        and its assistant reply is the delta JSON — neither belongs in
        training data). This is the source RDST's build_training_data reads.
        """
        pairs: list[dict] = []
        pending_user: str | None = None
        for msg in self._messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                pending_user = content
            elif role == "assistant" and pending_user is not None:
                pairs.append({"prompt": pending_user, "completion": content})
                pending_user = None
            # system messages and a trailing unanswered user msg are ignored

        # Drop the final pair if it is the delta-extraction exchange.
        delta_marker = "[SEEDLING DELTA EXTRACTION]"
        if pairs and delta_marker in pairs[-1]["prompt"]:
            pairs.pop()

        transcript_file = _BUFFER_DIR / f"transcript_{self.thread_id}.jsonl"
        with open(transcript_file, "w") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        logger.info(f"Transcript written: {transcript_file} ({len(pairs)} exchanges)")

    def _buffer_critic_eval(self, eval_: CriticEvaluation) -> None:
        """Write critic eval to session buffer file for crash recovery."""
        existing = []
        if self._buffer_file.exists():
            try:
                existing = json.loads(self._buffer_file.read_text())
            except Exception:
                pass

        from dataclasses import asdict
        existing.append(asdict(eval_))
        self._buffer_file.write_text(json.dumps(existing, default=str))

    def _log_event(self, event_type: str, data: dict) -> None:
        """Append a JSON event to the session log file."""
        log_file = _BUFFER_DIR / f"events_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# __main__ — interactive session
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import yaml
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
    else:
        config = {"model_name": "llama3.2", "critic_backend": "local"}

    mcm = MCM(base_model=config.get("model_name", "llama3.2"))
    critic = CriticInstance(
        backend=config.get("critic_backend", "local"),
        base_model=config.get("model_name", "llama3.2"),
    )

    session = ThreadSession(
        mcm=mcm,
        critic=critic,
        model_name=config.get("model_name", "llama3.2"),
        fresh="--fresh" in sys.argv,
    )

    print(session.start())
    print("\nSeedling session active. Type 'exit' to end.\n")

    try:
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue
            response = session.chat(user_input)
            print(f"\nModel: {response}\n")
    except KeyboardInterrupt:
        pass
    finally:
        delta = session.end()
        print(f"\n[Session ended. Insight: {delta.insight_gained[:80]}]")
        mcm.graceful_pause()
