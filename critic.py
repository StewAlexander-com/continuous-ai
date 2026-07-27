"""
seedling/critic.py — Internal Observer / Critic.

Two backends:
  "local"       — Second pass using Ollama base model (no adapter).
                  Same architecture, different context = weak but fast signal.
  "perplexity"  — Perplexity API (sonar or sonar-pro).
                  Genuinely different architecture = stronger independent signal.
                  Requires PERPLEXITY_API_KEY env var.

The Perplexity backend directly addresses the known weakness:
"The Critic is the same base model — it can't reliably evaluate itself."

Run as: python critic.py  → runs a sample evaluation and prints result.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path

from schemas import CriticEvaluation
from llm import InferenceBackend, get_default_backend

logger = logging.getLogger(__name__)


def _extract_json_block(raw: str) -> str:
    """Return the substring from the first '{' to the last '}', stripping any
    markdown fences or surrounding prose the model may have added."""
    raw = raw.strip()
    if raw.startswith("```"):
        # take content between the first pair of fences
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw


_NUM = r"[-+]?\d*\.?\d+"

def _lenient_fields(text: str) -> dict:
    """Field-by-field recovery when strict JSON parsing fails (e.g. an
    unescaped quote inside a string value). Pulls each known field with a
    targeted regex so one bad field (usually 'notes') doesn't discard the
    rest of a perfectly good evaluation."""
    out = {}
    m = re.search(r'"coherence"\s*:\s*(' + _NUM + r')', text)
    if m: out["coherence"] = float(m.group(1))
    m = re.search(r'"drift_risk"\s*:\s*(' + _NUM + r')', text)
    if m: out["drift_risk"] = float(m.group(1))
    m = re.search(r'"contradiction_detected"\s*:\s*(true|false)', text, re.I)
    if m: out["contradiction_detected"] = m.group(1).lower() == "true"
    m = re.search(r'"correction_predicted"\s*:\s*(true|false)', text, re.I)
    if m: out["correction_predicted"] = m.group(1).lower() == "true"
    # notes: grab everything after the key up to the last quote before } or EOL
    m = re.search(r'"notes"\s*:\s*"(.*)', text, re.S)
    if m:
        note = m.group(1).rsplit('"', 1)[0] if '"' in m.group(1) else m.group(1)
        out["notes"] = note.strip().rstrip(",").strip().strip('"')
    return out


def _parse_critic_payload(raw: str, backend: str) -> CriticEvaluation | None:
    """Robustly parse a critic JSON payload. Tries strict json.loads on the
    extracted block first, then falls back to lenient field recovery.
    Returns None only if not even 'coherence' can be recovered."""
    block = _extract_json_block(raw)
    data = None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        recovered = _lenient_fields(block)
        if "coherence" in recovered:
            logger.warning("Critic JSON was malformed; recovered fields leniently.")
            data = recovered
        else:
            return None
    try:
        return CriticEvaluation(
            response_id=str(uuid.uuid4()),
            coherence=max(0.0, min(1.0, float(data.get("coherence", 0.5)))),
            contradiction_detected=bool(data.get("contradiction_detected", False)),
            drift_risk=max(0.0, min(1.0, float(data.get("drift_risk", 0.0)))),
            correction_predicted=bool(data.get("correction_predicted", False)),
            notes=str(data.get("notes", "")),
            critic_backend=backend,
        )
    except (ValueError, KeyError):
        return None

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_CRITIC_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic_evaluation.txt"


def _load_critic_prompt() -> str:
    if _CRITIC_PROMPT_PATH.exists():
        return _CRITIC_PROMPT_PATH.read_text()
    # Inline fallback
    return """You are an independent evaluator assessing an AI model response.

Evaluate the following exchange:

USER QUERY:
[USER_QUERY]

MODEL RESPONSE:
[MODEL_RESPONSE]

Rate on these dimensions (0.0 to 1.0 unless noted):

1. coherence         — logical consistency and internal consistency
2. contradiction     — does the response contradict prior context? (true/false)
3. drift_risk        — does the response show reasoning style drift from prior sessions?
4. correction_predicted — would a careful user likely correct this response? (true/false)
5. notes             — one sentence summary of the primary quality signal

Return ONLY valid JSON:
{
  "coherence": 0.0,
  "contradiction_detected": false,
  "drift_risk": 0.0,
  "correction_predicted": false,
  "notes": "..."
}"""


# ---------------------------------------------------------------------------
# Local Critic (Ollama base model, no adapter)
# ---------------------------------------------------------------------------

def _evaluate_local(
    user_query: str,
    model_response: str,
    base_model: str,
    llm: InferenceBackend | None = None,
) -> CriticEvaluation:
    """Run critic pass using the local inference backend (no adapter loaded)."""
    backend = llm or get_default_backend()
    prompt = _load_critic_prompt()
    prompt = prompt.replace("[USER_QUERY]", user_query)
    prompt = prompt.replace("[MODEL_RESPONSE]", model_response)

    # num_ctx kept small so a separate critic can stay coresident with a large
    # chat model under OLLAMA_MAX_LOADED_MODELS>=2 (measured: gemma3:4b @ 2048
    # + qwen3:30b @ 8192 both stay loaded on 32GB; without this, every grade
    # evicts the chat model and the next turn pays a multi-second reload).
    response = backend.chat(
        model=base_model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0, "num_ctx": 2048},
        keep_alive="30m",
    )

    raw = response["message"]["content"].strip()

    result = _parse_critic_payload(raw, "local")
    if result is not None:
        return result
    logger.warning(f"Critic JSON unrecoverable. Raw: {raw[:200]}")
    return CriticEvaluation(
        response_id=str(uuid.uuid4()),
        coherence=0.5,
        notes="Critic parse error: response was not valid JSON",
        critic_backend="local",
    )


# ---------------------------------------------------------------------------
# Perplexity Critic (sonar / sonar-pro)
# ---------------------------------------------------------------------------

def _evaluate_perplexity(
    user_query: str,
    model_response: str,
    model: str = "sonar",
) -> CriticEvaluation:
    """
    Run critic pass using Perplexity API.

    Requires: PERPLEXITY_API_KEY environment variable.

    This gives a genuinely different architecture evaluating your local model —
    the strongest possible independent critic signal without another local model.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PERPLEXITY_API_KEY not set. "
            "Get a key at https://www.perplexity.ai/settings/api "
            "or fall back to --critic-backend local"
        )

    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx not installed. Run: pip install httpx")

    prompt = _load_critic_prompt()
    prompt = prompt.replace("[USER_QUERY]", user_query)
    prompt = prompt.replace("[MODEL_RESPONSE]", model_response)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise evaluation system. "
                    "Return only valid JSON. No explanation, no markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 300,
    }

    response = httpx.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()

    raw = response.json()["choices"][0]["message"]["content"].strip()

    result = _parse_critic_payload(raw, "perplexity")
    if result is not None:
        return result
    logger.warning(f"Perplexity critic JSON unrecoverable. Raw: {raw[:200]}")
    return CriticEvaluation(
        response_id=str(uuid.uuid4()),
        coherence=0.5,
        notes="Perplexity critic parse error: response was not valid JSON",
        critic_backend="perplexity",
    )


# ---------------------------------------------------------------------------
# CriticInstance — public interface
# ---------------------------------------------------------------------------

class CriticInstance:
    """
    Unified critic interface. Selects backend based on config.

    Usage:
        critic = CriticInstance(backend="perplexity", base_model="llama3.2")
        eval_ = critic.evaluate(user_query, model_response)
    """

    def __init__(
        self,
        backend: str = "local",
        base_model: str = "llama3.2",
        perplexity_model: str = "sonar",
        llm: InferenceBackend | None = None,
    ):
        self.backend = backend
        self.base_model = base_model
        self.perplexity_model = perplexity_model
        self.llm = llm or get_default_backend()

    def evaluate(self, user_query: str, model_response: str) -> CriticEvaluation:
        """
        Evaluate a model response. Returns CriticEvaluation.

        Falls back to local if Perplexity is unavailable — logs warning.
        Never raises: on total failure returns a neutral evaluation with notes.
        """
        try:
            if self.backend == "perplexity":
                return _evaluate_perplexity(
                    user_query, model_response, model=self.perplexity_model
                )
            else:
                return _evaluate_local(
                    user_query, model_response, self.base_model, llm=self.llm
                )
        except RuntimeError as e:
            if "PERPLEXITY_API_KEY" in str(e) or "httpx" in str(e):
                logger.warning(f"Perplexity critic unavailable, falling back to local: {e}")
                try:
                    return _evaluate_local(
                        user_query, model_response, self.base_model, llm=self.llm
                    )
                except Exception as e2:
                    logger.error(f"Local critic also failed: {e2}")
            else:
                logger.error(f"Critic evaluation failed: {e}")

            return CriticEvaluation(
                response_id=str(uuid.uuid4()),
                coherence=0.5,
                notes=f"Critic unavailable: {str(e)[:100]}",
                critic_backend=self.backend,
            )
        except Exception as e:
            logger.error(f"Unexpected critic error: {e}")
            return CriticEvaluation(
                response_id=str(uuid.uuid4()),
                coherence=0.5,
                notes=f"Critic error: {str(e)[:100]}",
                critic_backend=self.backend,
            )


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    critic = CriticInstance(backend="local", base_model="llama3.2")

    sample_query = "What is the Second Arrow doctrine in Buddhism?"
    sample_response = (
        "The Second Arrow refers to the suffering we add on top of pain through "
        "our own reactions — the pain of the arrow itself is the first arrow; "
        "our self-blame, catastrophizing, and rumination are the second. "
        "The practice is to accept the first arrow and not shoot the second."
    )

    print("Running critic evaluation (local backend)...")
    print("NOTE: requires Ollama running with llama3.2 pulled.")
    print("To use Perplexity backend: set PERPLEXITY_API_KEY env var\n")

    # Demonstrate the Perplexity path without actually calling it
    print("Perplexity backend available:", bool(os.environ.get("PERPLEXITY_API_KEY")))
    print("\nCriticInstance configured with backend:", critic.backend)
    print("Would evaluate query:", sample_query[:60])
