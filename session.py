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
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from schemas import ThreadDelta, CriticEvaluation, to_json
from mcm import MCM
from critic import CriticInstance, _extract_json_block
import storage

logger = logging.getLogger(__name__)


# Deterministic patterns that indicate the user stated a durable fact this
# session. Promotion is GATED on one of these matching a real user turn — we do
# NOT let the model self-report a fact (it could confabulate). Kind is inferred
# for the persona entry.
_USER_FACT_PATTERNS = [
    (r"\b(your name is|i (?:wish to |want to )?name you|call yourself|you (?:are|shall be) (?:called|named)|named you)\b", "identity"),
    (r"\b(remember that|please remember|don'?t forget|keep in mind|note that)\b", "preference"),
    (r"\b(i prefer|i like|i want you to|i'd like you to|from now on|always|never)\b", "preference"),
]


def _detect_user_stated_fact(user_turns: list[str]) -> str | None:
    """Return the 'kind' (identity|preference|constraint) if any of THIS session's
    user turns expresses a durable, memory-worthy statement; else None.

    This is the safety gate for persona promotion: the fact must trace to an
    actual user utterance, not the model's self-report.
    """
    import re
    blob = "\n".join(user_turns).lower()
    for pattern, kind in _USER_FACT_PATTERNS:
        if re.search(pattern, blob):
            return kind
    return None


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
        )

        self._messages = [{"role": "system", "content": system_prompt}]

        self._log_event("session_start", {
            "thread_id": self.thread_id,
            "model": self.model_name,
            "fresh": self.fresh,
        })

        logger.info(f"Session started: thread_id={self.thread_id} model={self.model_name} fresh={self.fresh}")
        return context_injection

    def chat(self, user_input: str) -> str:
        """
        Send a message, get response, pass to Critic, buffer eval, return response.

        Critic eval is buffered to disk — survives crashes.
        """
        try:
            import ollama
        except ImportError:
            raise RuntimeError("ollama package not installed. Run: pip install ollama")

        # Check for user correction signal (simple heuristic: explicit correction keywords)
        correction_keywords = [
            "that's wrong", "incorrect", "you said", "actually", "no,", "wait,",
            "that's not right", "mistake", "error",
        ]
        if any(kw in user_input.lower() for kw in correction_keywords):
            self._correction_count += 1
            logger.info(f"User correction detected (total: {self._correction_count})")

        self._messages.append({"role": "user", "content": user_input})

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

        # Persona promotion (Phase 1): if THIS session's user turns expressed a
        # durable fact, promote the extracted insight to the always-injected
        # persona layer. Gated on a real user utterance (not model self-report).
        user_turns = [m.get("content", "") for m in this_session_turns if m.get("role") == "user"]
        fact_kind = _detect_user_stated_fact(user_turns)
        if fact_kind and delta.insight_gained and "no insight" not in delta.insight_gained.lower():
            try:
                self.mcm.promote_persona_fact(delta.insight_gained, fact_kind, self.thread_id)
            except Exception as e:
                logger.error(f"Persona promotion failed: {e}")

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
