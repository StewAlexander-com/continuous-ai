"""
learning_ui.py — Customer-facing copy for Seedling's two learning tiers.

Single source of truth so :help, :learning, :tune status, and session-end
summaries stay aligned. Read-only display only — no learning behavior here.
"""


def format_learning_tiers_lines(*, expanded: bool = False) -> list[str]:
    """Return printable lines describing Tier 1 (L3) vs Tier 2 (LoRA).

    expanded=False — compact pointer for :help (keeps the command list scannable).
    expanded=True  — full guide for :learning and onboarding.
    """
    if not expanded:
        return [
            "How she learns (two tiers — most people only need Tier 1):",
            "  Tier 1 · automatic every session — already active; no action needed",
            "  Tier 2 · opt-in deep tuning — :tune status when ready; :learning for details",
        ]

    return [
        "How she learns — two tiers (read this once):",
        "",
        "  Tier 1 · Every session (automatic) — LIVE NOW",
        "    What changes: reasoning style + preferences (honesty-gated EMA).",
        "    What you do : nothing — updates when you exit each session.",
        "    Where to see: session-end Memory line, :dispositions, :tune status",
        "    Safe        : non-regressive — old signal decays, never wiped.",
        "",
        "  Tier 2 · Deep weight tuning (opt-in) — advanced, Apple Silicon",
        "    What changes: LoRA adapter trained on your best chat transcripts.",
        "    What you do : :tune status → :tune preview → eval gate must PASS",
        "                  → approve only via CLI:",
        "                    python seedling.py tune --approve-tuning",
        "    Safe        : never auto-runs; gate blocks risky jobs; explicit approval.",
        "    Honest note : tuned weights are not loaded in chat yet — Tier 1 is",
        "                  what shapes replies today.",
        "",
        "  Which tier?",
        "    Just chat normally — Tier 1 is already learning.",
        "    Chase Tier 2 only when :tune preview shows gate PASS and you want",
        "    a weight-level experiment beyond prompt/memory learning.",
    ]


def format_learning_commands_lines() -> list[str]:
    """Tune-related commands with tier context (for :help command list)."""
    return [
        "  :learning          how she learns (Tier 1 vs Tier 2 explained)",
        "  :tune status       learning progress + Tier 2 readiness",
        "  :tune preview      Tier 2 scoring + eval gate (read-only)",
    ]
