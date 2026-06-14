#!/usr/bin/env python3
"""One-shot persona correction.

Removes a confabulated durable fact (the false "Aida is the user's wife's name"
clause that a small model kept re-promoting) and replaces it with verified,
accurate facts. Run once, on the machine that owns .seedling_db:

    python3 fix_persona.py            # dry run: show what would change
    python3 fix_persona.py --apply    # apply and persist

Safe to re-run: it matches on substrings, so it won't duplicate corrections.
"""
import sys
from mcm import MCM
import storage

# Facts to REMOVE: any persona fact whose text contains one of these markers.
# Markers are chosen to match ONLY the confabulated assertion, not the
# corrected fact (which contains "not the user's wife").
REMOVE_MARKERS = [
    "it is also the user's wife's name",   # the exact confabulated clause
    "also the user's wife",
    "remember the information you discussed",   # contentless meta-directive noise
    "remember what we discussed",
    "i am not a person and not the user's wife.",  # superseded weaker identity wording
]

# Verified facts to ENSURE are present (added if missing, reinforced if present).
# Sourced from the user directly + StewAlexander.com/bio + GitHub (StewAlexander-com).
ENSURE_FACTS = [
    ("identity",
     "My name is Aida — it stands for 'AI Digital Assistant' and is ONLY my name as "
     "software. I am NOT a person and NOT the user's wife or partner. If my name "
     "resembles a human name, that is a coincidence — I must never imply any "
     "personal relationship with the user."),
    ("identity",
     "The user is Stew Alexander, based in Mebane, NC. He is an AI/ML Infrastructure "
     "and Network Security Engineer (15+ years; zero-trust, infrastructure-as-code, "
     "networking). He is NOT an astrobiologist or space-exploration researcher."),
    ("preference",
     "The user maintains python-tutor: an offline-first Python tutor with a local LLM "
     "(Gemma via Ollama), FastAPI + static PWA, code lab, and source-backed feedback. "
     "When helping with Python, favor clear, runnable, source-backed examples."),
    ("identity",
     "The user's GitHub is StewAlexander-com and his site is StewAlexander.com. He "
     "builds offline-first, privacy-preserving local-AI and security tools "
     "(e.g. continuous-ai, python-tutor, vsix-cve-scanner, nemomac)."),
    ("preference",
     "The user prefers answers in a BLUF (Bottom Line Up Front) + concise tl;dr "
     "style: lead with the key takeaway, then brief supporting detail."),
]

def main(apply: bool) -> None:
    mcm = MCM()
    mcm.restore_context()
    state = mcm.current_state()
    if state is None:
        print("No context state found — nothing to fix.")
        return
    persona = state.persona

    print("=== BEFORE ===")
    for i, f in enumerate(persona.facts):
        print(f"  [{i}] ({f.kind} x{f.reinforce_count}) {f.text}")

    # 1) Remove confabulated facts
    kept = []
    removed = []
    for f in persona.facts:
        ftl = f.text.lower()
        if any(m.lower() in ftl for m in REMOVE_MARKERS):
            removed.append(f)
        else:
            kept.append(f)
    persona.facts = kept

    # 2) Ensure verified facts (uses dedup/reinforce built into add_or_reinforce)
    for kind, text in ENSURE_FACTS:
        persona.add_or_reinforce(text, kind, source_thread_id="persona-correction")

    print("\n=== REMOVED ===")
    for f in removed:
        print(f"  ({f.kind}) {f.text}")
    print("\n=== AFTER ===")
    for i, f in enumerate(persona.facts):
        print(f"  [{i}] ({f.kind} x{f.reinforce_count}) {f.text}")

    if apply:
        storage.save_context_state(state)
        print("\n[applied] persona corrected and persisted.")
    else:
        print("\n[dry run] no changes saved. Re-run with --apply to persist.")

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
