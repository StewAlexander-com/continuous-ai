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
import queue
import re
import sys
import threading
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
    # Polite-REQUEST directive form: "can/could/will you remember to ...",
    # "please remember to ...". This is a standing instruction, NOT the recall
    # QUESTION "do you remember ...?" (excluded via the 'do you' negative
    # lookbehind-style guard below). Anchored on 'remember to' / 'remember that'.
    (r"\b(?:can|could|will|would)\s+you\s+remember\s+(?:to|that)\b", "preference"),
    (r"\b(from now on)\b", "constraint"),
    (r"(?:^|[.!?]\s+)(?:please\s+)?(?:always|never)\b", "constraint"),
]

# Recall QUESTIONS must never be promoted as directives — "do you remember our
# chat?", "can you remember what I said?" (asking about recall, not instructing).
_RECALL_QUESTION_RE = re.compile(
    r"\b(?:do|did|can|could)\s+you\s+remember\s+(?:what|when|where|who|how|why|if|our|the|that|my|me|us)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Behavioral guards injected into EVERY session's system prompt.
# Defined as a module constant so the runtime AND the confabulation eval
# (eval_confabulation.py) measure the EXACT SAME shipped text — no drift
# between what runs and what's tested.
# ---------------------------------------------------------------------------
_GUARD_TEXT = (
    # --- Capability boundary / no-confabulation guard ---
    # Seedling is fully offline: NO web/file access, NO retrieval tool. A small
    # model will happily *pretend* to fetch a URL and invent its contents.
    "CAPABILITY BOUNDARY (read carefully): You run fully offline. "
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
    # The assistant is named 'Aida'; a small model RE-DERIVES "Aida is the
    # user's wife" from the name alone. Stated explicitly every session.
    + "IDENTITY (do not confuse): Your name is Aida — it stands for "
    "'AI Digital Assistant' and is ONLY your name as a piece of "
    "software. You are NOT a person. You are NOT the user's wife, "
    "partner, or any human, and you must NEVER state or imply that you "
    "are. If your name resembles a human name, that is a coincidence — "
    "do not infer any personal relationship from it. The user is Stew "
    "Alexander; you are his AI assistant, nothing more."
    + "\n\n"
    # --- Scoped 'exact-title' hedge guard ---
    # Hedge ONLY exact titles of creative/published works; never identity,
    # user facts, concepts, code, or reasoning; never refuse to recommend.
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


def _extract_user_directives(user_turns: list[str]) -> list[tuple[str, str]]:
    """Return [(verbatim_text, kind), ...] for each user turn that issues a strong
    durable directive. Verbatim (whitespace-collapsed, capped). Empty list means
    no explicit directive this session — caller falls back to delta-based promotion.

    Safety: promotion traces to the user's ACTUAL words, never the model's
    self-report, so it cannot promote a confabulated fact.
    """
    out: list[tuple[str, str]] = []
    seen = set()
    for turn in user_turns:
        low = turn.lower()
        # Recall QUESTIONS ("do you remember our chat?") are never directives.
        if _RECALL_QUESTION_RE.search(low):
            continue
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
            # Cut at a clause boundary so we don't swallow a following clause.
            # Include contrastive ' not ' so "X is VSCode not Vim" yields
            # replacement='VSCode' (clean) and pushes 'Vim' into the 'wrong'
            # span, which actually HELPS locate the stale fact to prune.
            cut = re.split(r";|\s+and\s+|\s+not\s+", rest, maxsplit=1, flags=re.I)
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


# --- DOUBT-SCOPE GUARD: keep user-anchored facts OUT of the doubt machine -----
# Deliberation must only ever challenge the MODEL'S OWN inferences. Casting doubt
# on a fact the user stated about themselves (their name, location, job,
# preferences, or how they want the assistant to behave) is not 'real' doubt —
# the user is the authority on those, so doubting them is a category error, not
# epistemic humility. These patterns detect an insight that ASSERTS a
# user-anchored fact, so it can bypass deliberation and be recorded verbatim.
# Model-free and deterministic; this enforces the scope guarantee that the
# deliberation docs always promised but the code never checked.
_USER_FACT_PATTERNS = [
    r"\bthe user(?:'s|s)?\b",                 # "the user is", "the user's name"
    r"\buser (?:is|lives|works|prefers|wants|named|likes|named themselves)\b",
    r"\b(?:named|calls?) (?:me|the assistant|you)\b",   # naming the assistant
    r"\bmy name is\b", r"\bi (?:am|live|work|prefer|want|like)\b",
    r"\byour name is\b", r"\byou (?:are|should) (?:be )?(?:called|named)\b",
    r"\bwants? (?:me|you|the assistant) to\b",          # behavior directive
    r"\bprefers?\b.*\b(?:bluf|format|style|tone)\b",
]


def _asserts_user_fact(text: str) -> bool:
    """True if `text` reads as an assertion of a user-anchored fact (identity,
    location, job, preference, or behavior directive). Deterministic, no model.
    Used to keep such insights out of deliberation so 'doubt' is never
    manufactured about something the user is the authority on."""
    if not text:
        return False
    low = text.lower()
    return any(re.search(p, low) for p in _USER_FACT_PATTERNS)


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
        deliberation_enabled: bool = True,
        live_deliberation_enabled: bool = True,
        history_window_turns: int = 24,
    ):
        self.mcm = mcm
        self.critic = critic
        self.model_name = model_name
        self.fresh = fresh
        self.tuning_threshold_n = tuning_threshold_n
        self.deliberation_enabled = deliberation_enabled
        # Live (per-turn) deliberation runs in the BACKGROUND and never blocks a
        # reply. Distinct from end-of-session deliberation, which may think harder.
        self.live_deliberation_enabled = live_deliberation_enabled
        self.thread_id = str(uuid.uuid4())
        self._messages: list[dict] = []
        self._critic_evals: list[tuple[CriticEvaluation, str]] = []  # (eval, thread_id)
        self._correction_count = 0
        self._buffer_file = _BUFFER_DIR / f"session_{self.thread_id}.buffer.json"
        self._memory_notices: list[str] = []  # live persona-promotion confirmations for the CLI
        # Pending correction awaiting user disambiguation:
        #   {"replacement": <text|None>, "kind": <str>}  (which fact to prune is unknown)
        self._pending_correction: dict | None = None
        # --- background Critic: grading is a SECOND model call; running it off
        # the reply path is the single biggest responsiveness win. Jobs are
        # graded on a daemon thread; results land in _critic_evals (lock-guarded)
        # exactly as before, and end() joins this worker before it averages.
        self._critic_q: "queue.Queue" = queue.Queue()
        self._critic_worker: threading.Thread | None = None
        self._critic_lock = threading.Lock()
        # How many recent turns of transcript to send to the model each turn.
        # The FULL transcript is still persisted for RDST; this only bounds what
        # we re-feed so later turns don't slow as the conversation grows. The
        # system prompt (index 0) is ALWAYS kept. Generous so context is intact.
        self._history_window_turns = max(2, int(history_window_turns))  # >=1 exchange
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
            + _GUARD_TEXT
        )

        self._messages = [{"role": "system", "content": system_prompt}]

        self._log_event("session_start", {
            "thread_id": self.thread_id,
            "model": self.model_name,
            "fresh": self.fresh,
        })

        logger.info(f"Session started: thread_id={self.thread_id} model={self.model_name} fresh={self.fresh}")
        return context_injection

    def _chat_once(self, model: str, messages: list[dict]) -> str:
        """Stateless single-shot model call for deliberation voices. Separate
        from chat() so it never touches the conversation transcript or memory."""
        import ollama
        resp = ollama.chat(model=model, messages=messages)
        return resp["message"]["content"]

    def _model_window(self) -> list[dict]:
        """The messages actually SENT to the model this turn: the system prompt
        (always) plus the most recent N turns. The full transcript still lives in
        self._messages and is persisted for RDST — this only bounds what we
        re-feed so per-turn latency doesn't grow without limit as the chat goes
        long. No logic regression: nothing is dropped from memory, only from the
        re-sent context tail (and recency is what matters most for coherence)."""
        if not self._messages:
            return self._messages
        system = self._messages[:1]            # index 0 is the system prompt
        tail = self._messages[1:]
        if len(tail) <= self._history_window_turns:
            return self._messages
        return system + tail[-self._history_window_turns:]

    # ----- background Critic (off the reply path) --------------------------
    def _ensure_critic_worker(self) -> None:
        if self._critic_worker is None or not self._critic_worker.is_alive():
            self._critic_worker = threading.Thread(
                target=self._critic_run, name="critic-grader", daemon=True)
            self._critic_worker.start()

    def _critic_run(self) -> None:
        while True:
            job = self._critic_q.get()
            if job is None:                    # shutdown sentinel
                self._critic_q.task_done()
                return
            user_input, response_text = job
            try:
                eval_ = self.critic.evaluate(user_input, response_text)
                with self._critic_lock:
                    self._critic_evals.append((eval_, self.thread_id))
                    self._buffer_critic_eval(eval_)
                self._log_event("critic_eval", {
                    "thread_id": self.thread_id,
                    "critic_coherence": getattr(eval_, "coherence", None),
                    "critic_backend": getattr(eval_, "critic_backend", None),
                })
            except Exception as e:             # never let a bad grade kill grading
                logger.error(f"background critic failed: {e}")
            finally:
                self._critic_q.task_done()

    def _submit_critic(self, user_input: str, response_text: str) -> None:
        """Queue a turn for background grading. Non-blocking: the reply has
        already been returned to the user by the time this runs."""
        self._ensure_critic_worker()
        self._critic_q.put((user_input, response_text))

    def _join_critic(self, timeout: float = 30.0) -> None:
        """Block until all queued critic grades finish (bounded). Called by end()
        so the coherence average and the flush see every eval (no logic lost)."""
        if self._critic_worker is None:
            return
        try:
            import time
            deadline = time.monotonic() + timeout
            # Wait until the queue has no unfinished tasks (queued + in-flight),
            # bounded so exit never hangs on a stuck model call.
            while self._critic_q.unfinished_tasks > 0 and time.monotonic() < deadline:
                time.sleep(0.03)
        except Exception as e:
            logger.error(f"critic join error: {e}")

    def _live_deliberation_candidate(self, response_text: str) -> str | None:
        """Model-free gate: decide whether THIS turn produced a durable,
        model-derived insight worth deliberating in the background. Returns the
        candidate insight text, or None to skip (most chit-chat turns skip).

        Cheap and conservative on purpose: a background model run is only worth
        spending when the turn likely contains a claim that could enter memory.
        Two triggers:
          1) The model flagged its own observation with [EMERGENT] — extract it.
          2) A substantive declarative response (long enough to carry a claim).
        DOUBT-SCOPE GUARD: a candidate that merely restates a user-anchored fact
        (e.g. the model echoing "your name is Stew") is dropped here so it never
        enters the deliberation/doubt machine — the user owns those truths.
        """
        if not response_text:
            return None
        text = response_text.strip()
        candidate = None
        # (1) Prefer an explicitly emergent observation if the model marked one.
        if "[EMERGENT]" in text:
            # Take the sentence/line carrying the marker as the candidate claim.
            for line in text.splitlines():
                if "[EMERGENT]" in line:
                    claim = line.replace("[EMERGENT]", "").strip(" :->—\t")
                    if len(claim) >= 24:
                        candidate = claim
                        break
        # (2) Substantive declarative turn. Skip very short replies, pure
        #     questions, and trivial acknowledgements (low information).
        if candidate is None:
            if len(text) < 80:
                return None
            if text.endswith("?") and text.count(".") == 0:
                return None   # a clarifying question, not a claim
            first = text.split(". ")[0].strip()
            candidate = first if len(first) >= 24 else text[:240]
        # Doubt-scope guard: never deliberate a candidate that asserts or
        # resembles a user-stated fact.
        try:
            if _asserts_user_fact(candidate) or self.mcm.resembles_persona_fact(candidate):
                return None
        except Exception:
            pass   # on any check error, fall through (treat as model insight)
        return candidate

    def _promote_belief_from_delib(self, delib) -> None:
        """Funnel one Deliberation result into the cross-thread belief layer.
        This is how deliberation GROWS the context map: the surviving synthesis
        becomes an injected belief future threads can reinforce or revise. Both
        contested (high-information) and uncontested (low-information) results
        are admitted; the belief store's eviction policy lets the weak ones
        decay first, so what persists is what kept earning its place. Strictly
        model-derived. Never raises — belief growth must not break end()."""
        if delib is None:
            return
        try:
            text = (getattr(delib, "synthesis", "") or "").strip()
            # Skip empties and the failsafe-passthrough sentinel.
            if not text or getattr(delib, "antithesis", "") == "[deliberation unavailable]":
                return
            dissent = getattr(delib, "antithesis", "") if getattr(delib, "contested", False) else ""
            outcome = self.mcm.promote_belief(
                text=text,
                dissent=dissent,
                agreement=float(getattr(delib, "agreement", 0.5)),
                contested=bool(getattr(delib, "contested", False)),
                source_thread_id=self.thread_id,
            )
            if outcome in ("added", "evicted_then_added"):
                self._memory_notices.append("[memory: earned a deliberated belief]")
            elif outcome == "reinforced":
                self._memory_notices.append("[memory: reinforced a deliberated belief]")
        except Exception as e:
            logger.error(f"belief promotion skipped: {e}")

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

    def chat(self, user_input: str, on_token=None) -> str:
        """
        Send a message, get response, return it. Critic grading runs in the
        BACKGROUND (off the reply path); the eval still buffers to disk and is
        joined at end() before averaging — identical logic, just not blocking.

        on_token: optional callable(str). If given, the model's reply is
        STREAMED token-by-token to it as it generates (perceived latency drops
        to near-zero). The full response string is still returned regardless, so
        every caller and the memory-correction short-circuit are unchanged.
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

        # Send only a bounded recent window to the model (full transcript is
        # kept in self._messages and persisted) so latency stays flat as the
        # conversation grows.
        window = self._model_window()
        # keep_alive keeps the model resident between turns so we don't pay a
        # cold reload mid-conversation (cheap responsiveness win).
        if on_token is not None:
            # Stream tokens: the user sees text immediately instead of waiting
            # for the whole reply. We accumulate the full string to return.
            parts = []
            try:
                for chunk in ollama.chat(model=self.model_name, messages=window,
                                         stream=True, keep_alive="10m"):
                    tok = chunk.get("message", {}).get("content", "")
                    if tok:
                        parts.append(tok)
                        try:
                            on_token(tok)
                        except Exception:
                            pass   # a display callback must never break the turn
                response_text = "".join(parts)
            except Exception as e:
                logger.error(f"streaming failed, falling back to non-stream: {e}")
                resp = ollama.chat(model=self.model_name, messages=window, keep_alive="10m")
                response_text = resp["message"]["content"]
        else:
            response = ollama.chat(model=self.model_name, messages=window, keep_alive="10m")
            response_text = response["message"]["content"]

        self._messages.append({"role": "assistant", "content": response_text})

        # Critic pass moved OFF the reply path: queue it for background grading.
        # The eval still lands in _critic_evals + the disk buffer (lock-guarded),
        # and end() joins the grader before averaging — no logic lost, no wait.
        self._submit_critic(user_input, response_text)

        # Log emergent behavior immediately
        if "[EMERGENT]" in response_text:
            logger.warning(
                f"EMERGENT marker in response: thread={self.thread_id} "
                f"response_preview={response_text[:100]}"
            )

        # Note: critic_coherence/backend are logged from the BACKGROUND grader
        # now (the eval isn't ready synchronously), via a 'critic_eval' event.
        self._log_event("chat_turn", {
            "thread_id": self.thread_id,
            "user_input_len": len(user_input),
            "response_len": len(response_text),
            "emergent_in_response": "[EMERGENT]" in response_text,
        })

        # --- LIVE DELIBERATION (background, NON-BLOCKING) ---
        # Responsiveness is paramount during a conversation: the reply is already
        # computed and is returned below WITHOUT waiting on deliberation. If this
        # turn produced a durable, model-derived candidate insight, hand it to a
        # background worker that deliberates it (adaptive depth) and appends to
        # the same ledger. The end-of-session pass drains anything still in
        # flight. submit() never blocks.
        if self.live_deliberation_enabled:
            try:
                candidate = self._live_deliberation_candidate(response_text)
                if candidate:
                    from live_deliberation import get_runner
                    get_runner().submit(
                        candidate, self.thread_id, self._chat_once, self.model_name)
            except Exception as e:
                logger.error(f"live deliberation submit skipped: {e}")

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

        # Background critic grades may still be in flight — join them (bounded)
        # so the coherence average and the flush see EVERY eval. The grading
        # overlapped the conversation + delta extraction, so this rarely waits.
        self._join_critic(timeout=30.0)

        # Compute average coherence from critic evals this session (read snapshot
        # under the lock so a late background append can't race the iteration).
        with self._critic_lock:
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

        # --- DELIBERATION (3-voice, variance-gated) over the MODEL-DERIVED insight ---
        # Honest scope: this deliberates ONLY the model's own end-of-session
        # insight. User-anchored facts (directives/corrections) are promoted
        # live in chat() and NEVER pass through here — the user still owns truth.
        # Consensus is treated as suspect; a surviving objection earns the
        # belief and is preserved as dissent. Failsafe: any error => the raw
        # insight passes through unchanged (deliberation must never break end()).
        # Let background (live) deliberations finish before we add the end
        # record, so the ledger reflects the conversation in order. Bounded so
        # exit never hangs; the append lock makes any overlap safe regardless.
        live_delibs = []
        if self.live_deliberation_enabled:
            try:
                from live_deliberation import get_runner
                live_delibs = get_runner().collect_results(timeout=30.0)
            except Exception as e:
                logger.error(f"live deliberation collect skipped: {e}")

        raw_insight = str(data.get("insight_gained", "No insight extracted."))
        final_insight = raw_insight
        dissent = ""
        end_delib = None
        # DOUBT-SCOPE GUARD (enforces the long-promised scope guarantee): only
        # deliberate the MODEL'S OWN inferences. If the insight asserts a
        # user-anchored fact — either by phrasing or by resembling a stored
        # persona fact — it bypasses deliberation and is recorded VERBATIM. The
        # user is the authority on user facts; manufacturing doubt about them
        # ('uncertain whether Stew lives in Mebane') is a category error, not
        # real doubt. Genuine model claims still get fully deliberated, so real
        # doubt is preserved.
        is_user_fact = False
        try:
            is_user_fact = _asserts_user_fact(raw_insight) or \
                self.mcm.resembles_persona_fact(raw_insight)
        except Exception as e:
            logger.error(f"user-fact gate check failed (treating as model insight): {e}")
        if is_user_fact:
            logger.info("Insight is user-anchored — bypassing deliberation (recorded verbatim).")
        if self.deliberation_enabled and not is_user_fact and raw_insight and \
                raw_insight != "No insight extracted.":
            try:
                from deliberation import deliberate
                end_delib = deliberate(raw_insight, self.thread_id, self._chat_once, self.model_name)
                final_insight = end_delib.synthesis or raw_insight
                if end_delib.contested:
                    dissent = end_delib.antithesis
                logger.info(
                    f"Deliberation: contested={end_delib.contested} "
                    f"agreement={end_delib.agreement:.2f} thread={self.thread_id}"
                )
            except Exception as e:
                logger.error(f"Deliberation skipped (passthrough): {e}")

        # --- GROW THE CONTEXT MAP: promote deliberated beliefs across threads ---
        # Every surviving synthesis (live per-turn + the end pass) is promoted
        # into the L2b belief layer, which is injected into EVERY future thread.
        # This is the mechanism by which deliberation accumulates over time:
        # re-derived beliefs reinforce; the weakest decay out under the cap.
        # Strictly model-derived; persona (user truth) is untouched. Fail-safe.
        if self.deliberation_enabled:
            for d in [*live_delibs, *( [end_delib] if end_delib else [] )]:
                self._promote_belief_from_delib(d)

        delta = ThreadDelta(
            thread_id=self.thread_id,
            timestamp=datetime.now(timezone.utc),
            insight_gained=final_insight,
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

        # Flush critic evals to LanceDB (snapshot under the lock first).
        with self._critic_lock:
            evals_to_flush = list(self._critic_evals)
        for eval_, tid in evals_to_flush:
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
