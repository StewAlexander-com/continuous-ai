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
import uuid
from pathlib import Path

from schemas import CriticEvaluation

logger = logging.getLogger(__name__)

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
) -> CriticEvaluation:
    """Run critic pass using Ollama base model (no adapter loaded)."""
    try:
        import ollama
    except ImportError:
        raise RuntimeError("ollama package not installed. Run: pip install ollama")

    prompt = _load_critic_prompt()
    prompt = prompt.replace("[USER_QUERY]", user_query)
    prompt = prompt.replace("[MODEL_RESPONSE]", model_response)

    response = ollama.chat(
        model=base_model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},  # deterministic for evaluation
    )

    raw = response["message"]["content"].strip()

    try:
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return CriticEvaluation(
            response_id=str(uuid.uuid4()),
            coherence=float(data.get("coherence", 0.5)),
            contradiction_detected=bool(data.get("contradiction_detected", False)),
            drift_risk=float(data.get("drift_risk", 0.0)),
            correction_predicted=bool(data.get("correction_predicted", False)),
            notes=str(data.get("notes", "")),
            critic_backend="local",
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Critic JSON parse failed: {e}. Raw: {raw[:200]}")
        return CriticEvaluation(
            response_id=str(uuid.uuid4()),
            coherence=0.5,
            notes=f"Critic parse error: {str(e)[:100]}",
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

    # Strip any stray markdown
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw)
        return CriticEvaluation(
            response_id=str(uuid.uuid4()),
            coherence=float(data.get("coherence", 0.5)),
            contradiction_detected=bool(data.get("contradiction_detected", False)),
            drift_risk=float(data.get("drift_risk", 0.0)),
            correction_predicted=bool(data.get("correction_predicted", False)),
            notes=str(data.get("notes", "")),
            critic_backend="perplexity",
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Perplexity Critic JSON parse failed: {e}. Raw: {raw[:200]}")
        return CriticEvaluation(
            response_id=str(uuid.uuid4()),
            coherence=0.5,
            notes=f"Perplexity critic parse error: {str(e)[:100]}",
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
    ):
        self.backend = backend
        self.base_model = base_model
        self.perplexity_model = perplexity_model

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
                return _evaluate_local(user_query, model_response, self.base_model)
        except RuntimeError as e:
            if "PERPLEXITY_API_KEY" in str(e) or "httpx" in str(e):
                logger.warning(f"Perplexity critic unavailable, falling back to local: {e}")
                try:
                    return _evaluate_local(user_query, model_response, self.base_model)
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
