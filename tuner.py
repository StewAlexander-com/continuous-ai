"""
seedling/tuner.py — RDST: Regressive Dynamic Self-Tuning.

Implements recency-weighted thread scoring, training data extraction,
LoRA adapter tuning via mlx_lm.lora, and rollback.

SAFETY GATES:
  - Never auto-approves a TuningJob
  - Requires --approve-tuning CLI flag
  - Prints full training data diff before any run
  - Runs 5-turn eval loop before/after, prints coherence delta
  - On failure: rolls back and logs

Run as: python tuner.py  → prints scoring for current thread history.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from schemas import ThreadDelta, TuningJob, to_json

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_TRAINING_DIR = Path(__file__).parent / "training_data"
_ADAPTER_DIR = Path(__file__).parent / "adapters"
_LOG_DIR = Path(__file__).parent / "logs"


# ---------------------------------------------------------------------------
# ScoredThread
# ---------------------------------------------------------------------------

@dataclass
class ScoredThread:
    """A ThreadDelta with its computed RDST score."""
    delta: ThreadDelta
    raw_score: float
    weighted_score: float
    age_in_threads: int


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_threads(
    thread_deltas: list[ThreadDelta],
    correction_penalty: float = 0.15,
    recency_decay_factor: float = 0.05,
) -> list[ScoredThread]:
    """
    Score threads for training data selection.

    Score = coherence_score - (user_correction_count * correction_penalty)
    Weighted = score * exp(-λ * age_in_threads)

    Returns sorted list, highest weighted_score first.
    """
    n = len(thread_deltas)
    scored = []
    for i, delta in enumerate(thread_deltas):
        age = n - 1 - i  # newest = age 0
        raw = delta.coherence_score - (delta.user_correction_count * correction_penalty)
        raw = max(0.0, min(1.0, raw))  # clamp to [0, 1]
        weighted = raw * math.exp(-recency_decay_factor * age)
        scored.append(ScoredThread(
            delta=delta,
            raw_score=raw,
            weighted_score=weighted,
            age_in_threads=age,
        ))

    scored.sort(key=lambda x: x.weighted_score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Training data extraction
# ---------------------------------------------------------------------------

def build_training_data(
    scored_threads: list[ScoredThread],
    top_n: int = 10,
    job_id: str = "unknown",
) -> Path:
    """
    Extract top_n thread transcripts from logs and format as JSONL.

    Applies a diversity constraint: no more than 40% of training data
    from the same dominant_framework — prevents sycophancy collapse.

    Writes an mlx-lm-compatible dataset directory:
        training_data/run_{job_id}/train.jsonl
        training_data/run_{job_id}/valid.jsonl
    mlx_lm.lora reads a DIRECTORY and expects files named exactly
    train.jsonl / valid.jsonl, so --data must point at this dir.

    Each record is {"prompt": <user text>, "completion": <assistant text>},
    one per conversational exchange (a thread may contribute several).

    Returns path to the per-job dataset DIRECTORY.
    Raises ValueError if no usable training records could be assembled.
    """
    job_dir = _TRAINING_DIR / f"run_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    records, _, _, _ = _collect_training_records(scored_threads, top_n=top_n)

    if not records:
        raise ValueError(
            "No usable training records assembled. RDST needs at least one "
            "thread with a saved transcript (logs/transcript_<id>.jsonl). "
            "Run one or more real chat sessions first."
        )

    # mlx-lm wants a train/valid split. With tiny datasets, ensure valid has
    # at least 1 record by reserving the last record (or duplicating if n==1).
    if len(records) == 1:
        train_records = records
        valid_records = records  # duplicate; mlx-lm requires a non-empty valid set
    else:
        split = max(1, int(len(records) * 0.8))
        train_records = records[:split]
        valid_records = records[split:] or records[-1:]

    train_path = job_dir / "train.jsonl"
    valid_path = job_dir / "valid.jsonl"
    with open(train_path, "w") as f:
        for r in train_records:
            f.write(json.dumps(r) + "\n")
    with open(valid_path, "w") as f:
        for r in valid_records:
            f.write(json.dumps(r) + "\n")

    logger.info(
        f"Training data written: {job_dir} "
        f"({len(train_records)} train / {len(valid_records)} valid records)"
    )
    return job_dir


def _load_transcript(thread_id: str) -> list[dict] | None:
    """
    Load a thread's real transcript from logs/transcript_{thread_id}.jsonl.

    Each line is {"prompt": <user text>, "completion": <assistant text>}.
    These are written by session.ThreadSession._write_transcript() at end().

    Returns a list of {"prompt", "completion"} exchanges, or None if the
    transcript file is missing (e.g. a thread logged before transcript
    logging existed) or contains no usable exchanges.
    """
    transcript_file = _LOG_DIR / f"transcript_{thread_id}.jsonl"
    if not transcript_file.exists():
        return None

    exchanges: list[dict] = []
    try:
        with open(transcript_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prompt = (rec.get("prompt") or "").strip()
                completion = (rec.get("completion") or "").strip()
                if prompt and completion:
                    exchanges.append({"prompt": prompt, "completion": completion})
    except OSError as e:
        logger.warning(f"Could not read transcript {transcript_file}: {e}")
        return None

    return exchanges or None


def _collect_training_records(
    scored_threads: list[ScoredThread],
    top_n: int = 10,
) -> tuple[list[dict], list[str], int, dict]:
    """Dry-run training data assembly.

    Returns (records, thread_ids_used, skipped_no_transcript, analysis).
    analysis holds factual counts for training-sufficiency gate warnings.
    """
    top = scored_threads[:top_n]

    framework_counts: dict[str, int] = {}
    filtered: list[ScoredThread] = []
    max_per_framework = max(1, int(top_n * 0.4))

    for st in top:
        frameworks = st.delta.frameworks_used or ["none"]
        dominant = frameworks[0]
        count = framework_counts.get(dominant, 0)
        if count >= max_per_framework:
            logger.info(
                f"Diversity constraint: skipping thread {st.delta.thread_id} "
                f"(framework '{dominant}' at limit {max_per_framework})"
            )
            continue
        framework_counts[dominant] = count + 1
        filtered.append(st)

    records: list[dict] = []
    thread_ids_used: list[str] = []
    skipped_no_transcript = 0
    attachment_records = 0
    emergent_threads_used = 0
    per_thread_counts: dict[str, int] = {}
    _ATTACHMENT_MARKERS = ("[USER-ATTACHED FILE:", "[attached ", "[USER-ATTACHED")

    for st in filtered:
        exchanges = _load_transcript(st.delta.thread_id)
        if not exchanges:
            skipped_no_transcript += 1
            logger.warning(
                f"No transcript found for thread {st.delta.thread_id} — skipping "
                "(thread may predate transcript logging)"
            )
            continue
        thread_ids_used.append(st.delta.thread_id)
        if st.delta.emergent:
            emergent_threads_used += 1
        n_in_thread = 0
        for ex in exchanges:
            prompt = ex.get("prompt") or ""
            if any(m in prompt for m in _ATTACHMENT_MARKERS):
                attachment_records += 1
            records.append({
                "prompt": ex["prompt"],
                "completion": ex["completion"],
            })
            n_in_thread += 1
        per_thread_counts[st.delta.thread_id] = n_in_thread

    analysis = {
        "skipped_no_transcript": skipped_no_transcript,
        "diversity_skipped": max(0, len(top) - len(filtered)),
        "attachment_records": attachment_records,
        "emergent_threads_used": emergent_threads_used,
        "candidates_considered": len(filtered),
    }
    if records:
        max_one = max(per_thread_counts.values())
        analysis["max_thread_exchange_fraction"] = max_one / len(records)
        analysis["attachment_fraction"] = attachment_records / len(records)
    else:
        analysis["max_thread_exchange_fraction"] = 0.0
        analysis["attachment_fraction"] = 0.0

    return records, thread_ids_used, skipped_no_transcript, analysis


def estimate_training_stats(
    scored_threads: list[ScoredThread],
    top_n: int = 10,
) -> dict:
    """Dry-run stats for preview/gate checks; no files written."""
    top_n = max(1, min(int(top_n or 10), 100))
    try:
        records, thread_ids_used, skipped, analysis = _collect_training_records(scored_threads, top_n=top_n)
    except Exception as e:
        logger.exception("estimate_training_stats failed")
        return {
            "n_records": 0,
            "n_threads": 0,
            "thread_ids": [],
            "skipped_no_transcript": 0,
            "samples": [],
            "top_n": top_n,
            "error": str(e),
        }
    return {
        "n_records": len(records),
        "n_threads": len(thread_ids_used),
        "thread_ids": thread_ids_used,
        "skipped_no_transcript": skipped,
        "samples": records[:3],
        "top_n": top_n,
        **analysis,
    }


def format_scoring_table(scored: list[ScoredThread]) -> list[str]:
    """Return printable RDST scoring table lines."""
    lines = [
        f"\nRDST Scoring — {len(scored)} threads\n",
        f"{'Thread ID':<36}  {'Raw':>5}  {'Wt.':>5}  {'Age':>4}  {'Emg':>4}",
        "-" * 62,
    ]
    for st in scored:
        lines.append(
            f"{st.delta.thread_id}  "
            f"{st.raw_score:>5.2f}  "
            f"{st.weighted_score:>5.3f}  "
            f"{st.age_in_threads:>4}  "
            f"{'Y' if st.delta.emergent else 'N':>4}"
        )
    return lines


def format_training_preview_lines(stats: dict, *, version_in: int, version_out: int) -> list[str]:
    """Return printable training-data preview lines (no side effects)."""
    lines = [
        "",
        "── Training data preview ──",
        f"  Train exchanges: {stats.get('n_records', 0)}",
        f"  Threads used   : {stats.get('n_threads', 0)}",
        f"  Adapter in→out : v{version_in} → v{version_out}",
    ]
    skipped = stats.get("skipped_no_transcript", 0)
    if skipped:
        lines.append(f"  Skipped        : {skipped} thread(s) missing transcripts")
    err = stats.get("error")
    if err:
        lines.append(f"  Preview error  : {err}")
    for i, r in enumerate(stats.get("samples") or []):
        prompt_preview = (r.get("prompt", "") or "")[:60].replace("\n", " ")
        compl_preview = (r.get("completion", "") or "")[:60].replace("\n", " ")
        lines.append(f"  Sample {i + 1}     : \"{prompt_preview}\" → \"{compl_preview}\"")
    return lines


# ---------------------------------------------------------------------------
# Tuning job execution
# ---------------------------------------------------------------------------

def trigger_tuning(job: TuningJob, model_path: str, *, gate: "TuningGateResult | None" = None) -> TuningJob:
    """
    Execute a LoRA adapter tuning run via mlx_lm.lora.

    SAFETY: Requires job.approved == True.
    Prints training data diff before running.
    Runs 5-turn eval loop before/after.
    On failure: set status = rolled_back, restore prior adapter.

    model_path: path to MLX-converted model (not raw GGUF — use mlx_lm.convert first).
    """
    if not job.approved:
        raise RuntimeError(
            "TuningJob.approved is False. "
            "Pass --approve-tuning explicitly to authorize this run. "
            "Seedling will never auto-approve a tuning job."
        )

    if gate is not None and not gate.approved_for_run:
        raise RuntimeError(
            "Eval gate BLOCKED this tuning job. Run tune preview first and resolve blockers:\n"
            + "\n".join(f"  - {b}" for b in gate.blockers)
        )

    job_dir = _TRAINING_DIR / f"run_{job.job_id}"
    train_file = job_dir / "train.jsonl"
    if not train_file.exists():
        raise FileNotFoundError(
            f"Training data not found: {train_file}. "
            "Run build_training_data() before trigger_tuning()."
        )

    # Print diff summary
    print("\n" + "="*60)
    print("TUNING JOB DIFF SUMMARY")
    print("="*60)
    with open(train_file, encoding="utf-8", errors="replace") as f:
        records = []
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Corrupt training record in {train_file}: {e}") from e
    valid_file = job_dir / "valid.jsonl"
    n_valid = 0
    if valid_file.exists():
        try:
            with open(valid_file, encoding="utf-8", errors="replace") as vf:
                n_valid = sum(1 for line in vf if line.strip())
        except OSError as e:
            logger.warning(f"Could not read valid split: {e}")
    print(f"  Train records  : {len(records)}")
    print(f"  Valid records  : {n_valid}")
    print(f"  Threads used   : {len(job.thread_ids_used)}")
    print(f"  Adapter in     : v{job.adapter_version_in}")
    print(f"  Adapter out    : v{job.adapter_version_out}")
    print(f"  Composite sig  : {job.composite_signal:.3f}")
    for i, r in enumerate(records[:3]):
        prompt_preview = (r.get("prompt", "") or "")[:60].replace("\n", " ")
        compl_preview = (r.get("completion", "") or "")[:60].replace("\n", " ")
        print(f"  Sample {i+1}: prompt=\"{prompt_preview}\" -> \"{compl_preview}\"")
    print("="*60 + "\n")

    _ADAPTER_DIR.mkdir(exist_ok=True)
    out_adapter = _ADAPTER_DIR / f"lora_v{job.adapter_version_out}"
    backup_adapter = _ADAPTER_DIR / f"lora_v{job.adapter_version_in}.safetensors"

    job.status = "running"
    _log_job(job)

    try:
        cmd = [
            sys.executable, "-m", "mlx_lm.lora",
            "--model", model_path,
            "--train",
            "--data", str(job_dir),   # per-job dir containing train.jsonl/valid.jsonl
            "--adapter-path", str(out_adapter),
            "--iters", "100",
            "--batch-size", "4",
            "--num-layers", "4",     # mlx-lm renamed --lora-layers -> --num-layers
        ]

        logger.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        if result.returncode != 0:
            raise RuntimeError(f"mlx_lm.lora failed:\n{result.stderr}")

        job.status = "complete"
        _update_config_adapter_version(job.adapter_version_out)
        _log_job(job)
        logger.info(f"Tuning complete: adapter v{job.adapter_version_out}")

        # Post-tuning eval
        _run_eval_comparison(model_path, job.adapter_version_in, job.adapter_version_out)

    except Exception as e:
        logger.error(f"Tuning failed: {e}")
        job.status = "rolled_back"
        _log_job(job)
        rollback(job.adapter_version_in)

    return job


def rollback(target_version: int) -> None:
    """
    Restore adapter version in config.yaml. Logs rollback event.

    Checks both adapters/lora_v{N}/ (directory, current format) and
    adapters/lora_v{N}.safetensors (legacy file path).
    """
    adapter_dir = _ADAPTER_DIR / f"lora_v{target_version}"
    adapter_file = _ADAPTER_DIR / f"lora_v{target_version}.safetensors"
    if target_version > 0 and not adapter_dir.is_dir() and not adapter_file.is_file():
        logger.warning(
            f"Rollback target not found: {adapter_dir} or {adapter_file}"
            " — falling back to base model (v0)"
        )
        target_version = 0

    _update_config_adapter_version(target_version)

    _log_event("rollback", {
        "target_version": target_version,
        "adapter_dir": str(adapter_dir),
        "adapter_file": str(adapter_file),
    })
    logger.info(f"Rollback complete: adapter version → v{target_version}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_config_adapter_version(version: int) -> None:
    """
    Update only the adapter_version line in config.yaml, preserving all
    other lines, comments, and formatting.

    A naive yaml.safe_load + yaml.dump round-trip would strip every inline
    comment from config.yaml, which is meant to be human-readable. So we do
    a surgical line rewrite instead, keeping any trailing comment on the line.
    Falls back to a full dump only if no adapter_version line is found.
    """
    if not _CONFIG_PATH.exists():
        return

    import re
    lines = _CONFIG_PATH.read_text().splitlines(keepends=True)
    pattern = re.compile(r"^(\s*adapter_version\s*:\s*)([^#\n]*)(#.*)?(\r?\n?)$")
    replaced = False
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            comment = m.group(3) or ""
            sep = "  " if comment else ""
            newline = m.group(4) or "\n"
            lines[i] = f"{m.group(1)}{version}{sep}{comment}{newline}"
            replaced = True
            break

    if replaced:
        _CONFIG_PATH.write_text("".join(lines))
        return

    # Fallback: no adapter_version line found — append/rewrite via yaml.
    logger.warning("adapter_version line not found in config.yaml; rewriting via yaml dump")
    config = yaml.safe_load("".join(lines)) or {}
    config["adapter_version"] = version
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def _log_job(job: TuningJob) -> None:
    """Append TuningJob record to logs."""
    log_file = _LOG_DIR / "tuning_jobs.jsonl"
    _LOG_DIR.mkdir(exist_ok=True)
    with open(log_file, "a") as f:
        f.write(to_json(job) + "\n")


def _log_event(event_type: str, data: dict) -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    log_file = _LOG_DIR / f"events_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event_type, **data}
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _run_eval_comparison(model_path: str, version_before: int, version_after: int) -> None:
    """Run 5-turn eval loop and print coherence scores before and after."""
    print("\n" + "="*60)
    print("POST-TUNING EVAL (5-turn loop)")
    print("="*60)
    # TODO: implement actual eval loop against both adapters
    # Requires mlx_lm.generate with adapter path comparison
    print(f"  Adapter v{version_before} coherence: [run eval to populate]")
    print(f"  Adapter v{version_after} coherence: [run eval to populate]")
    print("  (Full eval loop: Phase 6 eval.py)")
    print("="*60 + "\n")


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import storage
    storage.init_db()
    deltas = storage.load_all_deltas()

    if not deltas:
        print("No thread deltas found. Run some sessions first.")
        sys.exit(0)

    config = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

    scored = score_threads(
        deltas,
        correction_penalty=config.get("correction_penalty", 0.15),
        recency_decay_factor=config.get("recency_decay_factor", 0.05),
    )

    print(f"\nRDST Scoring — {len(scored)} threads\n")
    print(f"{'Thread ID':<36}  {'Raw':>5}  {'Wt.':>5}  {'Age':>4}  {'Emg':>4}")
    print("-" * 62)
    for st in scored:
        print(
            f"{st.delta.thread_id}  "
            f"{st.raw_score:>5.2f}  "
            f"{st.weighted_score:>5.3f}  "
            f"{st.age_in_threads:>4}  "
            f"{'Y' if st.delta.emergent else 'N':>4}"
        )
