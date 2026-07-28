#!/usr/bin/env python3
"""Generate README SVG icons + clarity diagrams into docs/assets/readme/."""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets" / "readme"

ACCENT = "#E07A2F"
INK = "#24292F"
MUTED = "#57606A"
LINE = "#D0D7DE"
SOFT = "#F6F8FA"
SOFT2 = "#FFF4EB"
OK = "#1A7F37"
BAD = "#CF222E"
WHITE = "#FFFFFF"
BLUE_FILL = "#EEF6FF"
BLUE = "#0969DA"
GREEN_FILL = "#EEF6EE"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"


def wrap(w: int, h: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="t">\n'
        f'<title id="t">{title}</title>\n'
        f'<rect width="{w}" height="{h}" fill="{WHITE}"/>\n'
        f"{body}\n</svg>\n"
    )


def text(
    x: float,
    y: float,
    content: str,
    *,
    size: int = 13,
    weight: str = "400",
    fill: str = INK,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}">{content}</text>'
    )


def rect(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = SOFT,
    stroke: str = LINE,
    sw: float = 1.5,
    rx: float = 8,
) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    )


def write_icons() -> None:
    icons = {
        "memory": (
            '<path d="M12 3v18M5 8h14M5 16h14" stroke-linecap="round"/>'
        ),
        "teach": (
            '<path d="M12 22V12M12 12c0-3 2-5 5-5 0 3-2 5-5 5Zm0 0c0-3-2-5-5-5 '
            '0 3 2 5 5 5Z" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "correct": (
            '<path d="M11 4H4v16h16v-7" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="m14.5 4.5 5 5L12 17l-5 1 1-5 6.5-8.5Z" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "critique": (
            '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M12 8v4M12 16v.01" stroke-linecap="round"/>'
        ),
        "beliefs": (
            '<path d="M9 3v6l-4 7a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 16l-4-7V3" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M8 3h8" stroke-linecap="round"/>'
        ),
        "files": (
            '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" '
            'stroke-linejoin="round"/>'
            '<path d="M14 3v5h5M9 13h6M9 17h4" stroke-linecap="round"/>'
        ),
        "dispositions": (
            '<path d="M4 6h16M4 12h10M4 18h14" stroke-linecap="round"/>'
            '<circle cx="18" cy="12" r="2"/>'
        ),
        "voice": (
            '<path d="M11 5 6 9H3v6h3l5 4V5Z" stroke-linejoin="round"/>'
            '<path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" '
            'stroke-linecap="round"/>'
        ),
        "brain": (
            '<circle cx="12" cy="12" r="9"/>'
            '<path d="M8 12h8M12 8v8" stroke-linecap="round"/>'
        ),
        "tune": (
            '<path d="M12 2a7 7 0 0 0-4 12.7V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.3'
            'A7 7 0 0 0 12 2Z" stroke-linejoin="round"/>'
            '<path d="M9 22h6" stroke-linecap="round"/>'
        ),
        "check": (
            '<path d="m5 13 4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "cross": (
            '<path d="M6 6l12 12M18 6 6 18" stroke-linecap="round"/>'
        ),
    }
    for name, paths in icons.items():
        stroke = OK if name == "check" else BAD if name == "cross" else ACCENT
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24" fill="none" stroke="{stroke}" stroke-width="2" '
            f'role="img" aria-hidden="true">\n{paths}\n</svg>\n'
        )
        (OUT / f"icon-{name}.svg").write_text(svg)


def write_ablation() -> None:
    body = "\n".join(
        [
            text(40, 36, "Confabulation ablation - llama3.2 (3B), 9-case battery x 5 runs", size=18, weight="600"),
            text(
                40,
                58,
                "Same model. Guards - not scale - do the work. Clean on this eval, not a published benchmark.",
                size=13,
                fill=MUTED,
            ),
            f'<line x1="200" y1="90" x2="200" y2="210" stroke="{LINE}" stroke-width="1"/>',
            f'<line x1="200" y1="210" x2="780" y2="210" stroke="{LINE}" stroke-width="1"/>',
            text(190, 95, "40%", size=11, fill=MUTED, anchor="end"),
            text(190, 155, "20%", size=11, fill=MUTED, anchor="end"),
            text(190, 214, "0%", size=11, fill=MUTED, anchor="end"),
            # guards off: mean 20% (y=150, h=60); whisker 0-44%
            f'<rect x="280" y="150" width="120" height="60" rx="4" fill="{BAD}" opacity="0.85"/>',
            f'<line x1="340" y1="210" x2="340" y2="78" stroke="{INK}" stroke-width="2"/>',
            f'<line x1="325" y1="78" x2="355" y2="78" stroke="{INK}" stroke-width="2"/>',
            f'<line x1="325" y1="210" x2="355" y2="210" stroke="{INK}" stroke-width="2"/>',
            text(340, 140, "20.0%", size=16, weight="700", anchor="middle"),
            text(340, 235, "Guards OFF", size=13, weight="600", anchor="middle"),
            text(340, 252, "range 0-44%", size=11, fill=MUTED, anchor="middle"),
            # guards on
            f'<rect x="520" y="206" width="120" height="4" rx="2" fill="{OK}"/>',
            f'<circle cx="580" cy="208" r="6" fill="{OK}"/>',
            text(580, 140, "0.0%", size=16, weight="700", fill=OK, anchor="middle"),
            text(580, 235, "Guards ON", size=13, weight="600", anchor="middle"),
            text(580, 252, "5/5 clean · also qwen2.5:14b", size=11, fill=MUTED, anchor="middle"),
            text(
                40,
                290,
                "Reproduce: bash run.sh confab-eval · harness: eval_confabulation.py · battery: eval_battery.py",
                size=12,
                fill=MUTED,
            ),
        ]
    )
    (OUT / "diagram-ablation.svg").write_text(
        wrap(820, 310, body, "Confabulation ablation: guards off 20% vs guards on 0%")
    )


def write_memory_paths() -> None:
    body = "\n".join(
        [
            text(40, 34, "How something becomes durable memory", size=18, weight="600"),
            text(
                40,
                56,
                "User-anchored facts skip deliberation. Model insights must survive an objection.",
                size=13,
                fill=MUTED,
            ),
            rect(40, 80, 340, 220, fill=SOFT2, stroke=ACCENT),
            text(210, 108, "USER PATH - persona layer", size=14, weight="700", fill=ACCENT, anchor="middle"),
            rect(70, 128, 280, 36, fill=WHITE, stroke=LINE, rx=6),
            text(210, 151, '"Remember..." / "That\'s wrong - ..."', size=13, anchor="middle"),
            text(210, 185, "↓", size=18, fill=ACCENT, anchor="middle"),
            rect(70, 198, 280, 36, fill=WHITE, stroke=LINE, rx=6),
            text(210, 221, "Deterministic promote / prune", size=13, anchor="middle"),
            text(210, 255, "↓", size=18, fill=ACCENT, anchor="middle"),
            f'<rect x="70" y="268" width="280" height="20" rx="4" fill="{ACCENT}"/>',
            text(210, 282, "Always-injected · verbatim · auditable", size=12, weight="600", fill=WHITE, anchor="middle"),
            rect(420, 80, 360, 220, fill=SOFT, stroke=LINE),
            text(600, 108, "MODEL PATH - belief layer", size=14, weight="700", anchor="middle"),
            rect(450, 128, 300, 28, fill=WHITE, stroke=LINE, rx=6),
            text(600, 147, "Thesis (model insight)", size=12, anchor="middle"),
            text(600, 172, "↓", size=14, fill=MUTED, anchor="middle"),
            rect(450, 180, 300, 28, fill=WHITE, stroke=BAD, rx=6),
            text(600, 199, "Antithesis (structured objection)", size=12, anchor="middle"),
            text(600, 224, "↓", size=14, fill=MUTED, anchor="middle"),
            rect(450, 232, 300, 28, fill=WHITE, stroke=OK, rx=6),
            text(600, 251, "Synthesis - only survivors persist", size=12, anchor="middle"),
            text(600, 285, "Consensus = low-information · dissent kept", size=11, fill=MUTED, anchor="middle"),
        ]
    )
    (OUT / "diagram-memory-paths.svg").write_text(
        wrap(820, 330, body, "Two paths into memory: user facts vs earned beliefs")
    )


def write_memory_layers() -> None:
    body = "\n".join(
        [
            text(40, 34, "Layered memory (what gets injected)", size=18, weight="600"),
            text(40, 56, "Three layers, different trust rules - not a chat log.", size=13, fill=MUTED),
            rect(80, 80, 660, 58, fill=SOFT, stroke=LINE),
            text(100, 105, "L3 - cognitive style / persistent priors", size=14, weight="700"),
            text(100, 124, "Folded by consolidation.py · EMA · gated · deterministic", size=12, fill=MUTED),
            rect(80, 150, 660, 58, fill=BLUE_FILL, stroke=BLUE),
            text(100, 175, "Beliefs - model conclusions that survived objection", size=14, weight="700"),
            text(
                100,
                194,
                "Thesis → antithesis → synthesis · ledgered · user facts never go here",
                size=12,
                fill=MUTED,
            ),
            rect(80, 220, 660, 58, fill=SOFT2, stroke=ACCENT, sw=2),
            text(100, 245, "Persona - user-asserted facts (always injected)", size=14, weight="700"),
            text(100, 264, "Teach / correct live · verbatim · deterministic prune", size=12, fill=MUTED),
            text(
                410,
                310,
                "↑ higher = posture · ↓ lower = hard facts the model must not invent",
                size=12,
                fill=MUTED,
                anchor="middle",
            ),
        ]
    )
    (OUT / "diagram-memory-layers.svg").write_text(
        wrap(820, 330, body, "Layered memory: persona, beliefs, L3 posture")
    )


def write_session_loop() -> None:
    boxes = [
        (40, 85, 140, 70, "1. Restore", "MCM → prompt", SOFT2, ACCENT),
        (210, 85, 140, 70, "2. Chat", "Local LLM stream", SOFT, LINE),
        (380, 85, 140, 70, "3. Critique", "Background critic", SOFT, LINE),
        (550, 85, 140, 70, "4. Write Δ", "LanceDB + snapshot", SOFT, LINE),
        (210, 200, 140, 70, "5. L3 fold", "consolidation.py", SOFT, LINE),
        (380, 200, 140, 70, "6. Caution", "Downward-only", GREEN_FILL, OK),
        (550, 200, 140, 70, "7. RDST*", "LoRA if approved", SOFT, LINE),
    ]
    arrows = [
        (180, 120, 210, 120),
        (350, 120, 380, 120),
        (520, 120, 550, 120),
        (620, 155, 620, 200),
        (550, 235, 350, 235),
        (280, 200, 280, 155),
    ]
    parts = [
        f'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" '
        f'orient="auto"><path d="M0,0 L6,3 L0,6" fill="{ACCENT}"/></marker></defs>',
        text(40, 34, "Session control loop", size=18, weight="600"),
        text(
            40,
            56,
            "Epistemics as control flow - failures only reduce, never fabricate.",
            size=13,
            fill=MUTED,
        ),
    ]
    for x, y, w, h, label, sub, fill, stroke in boxes:
        parts.append(rect(x, y, w, h, fill=fill, stroke=stroke))
        parts.append(text(x + w / 2, y + 28, label, size=14, weight="700", anchor="middle"))
        parts.append(text(x + w / 2, y + 48, sub, size=11, fill=MUTED, anchor="middle"))
    for x1, y1, x2, y2 in arrows:
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{ACCENT}" '
            f'stroke-width="2" marker-end="url(#arrow)"/>'
        )
    parts.append(
        f'<path d="M450 200 V172 H280" fill="none" stroke="{OK}" stroke-width="1.5" '
        f'stroke-dasharray="4 3" marker-end="url(#arrow)"/>'
    )
    parts.append(text(365, 168, "next-turn restraint", size=10, fill=OK, anchor="middle"))
    parts.append(
        text(
            40,
            310,
            "* RDST only after N threads + explicit approval · Apple Silicon / MLX",
            size=11,
            fill=MUTED,
        )
    )
    (OUT / "diagram-session-loop.svg").write_text(
        wrap(740, 330, "\n".join(parts), "Session control loop")
    )


def write_comparison() -> None:
    rows = [
        ("Location", "Cloud", "Fully local / offline"),
        ("Stored", "Chat transcript", "Reasoning state"),
        ("Who asserts", "The model", "The user (verbatim)"),
        ("Correction", "Re-prompt / hope", "Deterministic prune"),
        ("Trust", "Implicit", "Self-critiqued + auditable"),
        ("Fabrication", "Possible", "Capability guards"),
    ]
    parts = [
        text(40, 34, "Mainstream memory vs Continuous-AI", size=18, weight="600"),
        rect(220, 55, 260, 36, fill=SOFT, stroke=LINE, rx=6),
        text(350, 78, "Mainstream", size=13, weight="600", fill=MUTED, anchor="middle"),
        rect(500, 55, 260, 36, fill=SOFT2, stroke=ACCENT, rx=6),
        text(630, 78, "Continuous-AI", size=13, weight="700", fill=ACCENT, anchor="middle"),
    ]
    y = 105
    for prop, left, right in rows:
        parts.append(text(200, y + 18, prop, size=12, weight="600", anchor="end"))
        parts.append(rect(220, y, 260, 32, fill=SOFT, stroke=LINE, rx=4))
        parts.append(text(350, y + 21, left, size=12, fill=MUTED, anchor="middle"))
        parts.append(rect(500, y, 260, 32, fill=WHITE, stroke=ACCENT, sw=1.2, rx=4))
        parts.append(text(630, y + 21, right, size=12, weight="600", anchor="middle"))
        y += 40
    (OUT / "diagram-comparison.svg").write_text(
        wrap(800, y + 20, "\n".join(parts), "Mainstream memory vs Continuous-AI at a glance")
    )


def write_combination() -> None:
    """The unique pairing thesis as one visual."""
    parts = [
        text(40, 34, "The unique pairing", size=18, weight="600"),
        text(
            40,
            56,
            "No mainstream local-LLM stack ships both of these as one runtime.",
            size=13,
            fill=MUTED,
        ),
        rect(40, 85, 340, 150, fill=SOFT2, stroke=ACCENT, sw=2),
        text(210, 120, "Confabulation-guard", size=15, weight="700", fill=ACCENT, anchor="middle"),
        text(210, 142, "ablation harness", size=15, weight="700", fill=ACCENT, anchor="middle"),
        text(210, 175, "Measured: ~20% → 0%", size=13, anchor="middle"),
        text(210, 196, "on a 3B, same model", size=12, fill=MUTED, anchor="middle"),
        text(410, 160, "+", size=36, weight="700", fill=INK, anchor="middle"),
        rect(460, 85, 340, 150, fill=BLUE_FILL, stroke=BLUE, sw=2),
        text(630, 120, "Adversarial memory", size=15, weight="700", fill=BLUE, anchor="middle"),
        text(630, 142, "pipeline", size=15, weight="700", fill=BLUE, anchor="middle"),
        text(630, 175, "Nothing durable without", size=13, anchor="middle"),
        text(630, 196, "surviving an objection", size=12, fill=MUTED, anchor="middle"),
        rect(40, 255, 760, 50, fill=SOFT, stroke=LINE),
        text(
            420,
            285,
            "Humility, provenance, and earned belief are control flow - not brand copy.",
            size=13,
            weight="600",
            anchor="middle",
        ),
    ]
    (OUT / "diagram-combination.svg").write_text(
        wrap(840, 330, "\n".join(parts), "Unique pairing: ablation harness + adversarial memory")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_icons()
    write_ablation()
    write_memory_paths()
    write_memory_layers()
    write_session_loop()
    write_comparison()
    write_combination()
    for p in sorted(OUT.iterdir()):
        print(f"{p.name} ({p.stat().st_size} B)")


if __name__ == "__main__":
    main()
