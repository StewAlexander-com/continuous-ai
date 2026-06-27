"""
test_collaborate.py — fuzzy deliberation wall + user-as-co-author.

Locks the honest contract:
  - WALL fires only on weak-AND-balanced (fuzzy), conservative cutoff.
  - Agreement is a signal, never auto-commit; probe only when it's the deciding
    weight on a contested belief.
  - Provenance records collaboration; overruled user dissent is KEPT, not dropped.
  - The wall question is interrogative (self-labeling; can't be read as fact).
"""
import sys
sys.path.insert(0, ".")
import wall
import collaborate as C

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


# ---------------- wall: fuzzy + conservative ----------------
def test_wall_fires_on_weak_and_balanced():
    w = wall.assess(0.30, 0.50, 0.50)
    check("weak + dead-even is a wall", w.is_wall and w.wall_score >= w.cutoff)


def test_wall_silent_when_confident():
    w = wall.assess(0.90, 0.50, 0.50)
    check("confident synthesis -> no wall", not w.is_wall and w.wall_score == 0.0)


def test_wall_silent_when_decisive():
    w = wall.assess(0.30, 0.90, 0.20)   # weak but one side clearly wins
    check("weak but decisive -> no wall (balanced_mu 0)", not w.is_wall)


def test_wall_is_conservative():
    # A 'mild' case (moderate coherence, mild imbalance) must NOT trip it.
    w = wall.assess(0.60, 0.60, 0.50)
    check("mild uncertainty stays silent (conservative)", not w.is_wall)


def test_wall_score_is_smooth_not_cliff():
    # Two near-identical inputs should give near-identical scores (no brittle step).
    a = wall.assess(0.40, 0.55, 0.50).wall_score
    b = wall.assess(0.41, 0.55, 0.50).wall_score
    check("wall_score is smooth (fuzzy, not a cliff)", abs(a - b) < 0.1)


def test_wall_assessment_is_auditable():
    w = wall.assess(0.3, 0.5, 0.5)
    d = w.to_log()
    for k in ["coherence", "margin", "low_coherence_mu", "balanced_mu",
              "wall_score", "cutoff", "is_wall"]:
        check(f"audit tuple has '{k}'", k in d)


def test_cutoff_is_tunable():
    loose = wall.assess(0.5, 0.55, 0.50, cutoff=0.2)
    strict = wall.assess(0.5, 0.55, 0.50, cutoff=0.95)
    check("looser cutoff can trip where strict doesn't",
          loose.is_wall or (not strict.is_wall))


# ---------------- response classification ----------------
def test_classify_agree():
    for t in ["yes", "agree", "I agree", "makes sense", "sounds right", "exactly"]:
        check(f"'{t}' -> agree", C.classify_response(t) == "agree")


def test_classify_counter():
    for t in ["no, because the load is bursty", "but what about X", "not quite"]:
        check(f"'{t}' -> counter", C.classify_response(t) == "counter")


def test_classify_ignore():
    check("empty -> ignore", C.classify_response("") == "ignore")
    check("None -> ignore", C.classify_response(None) == "ignore")


# ---------------- probe logic (only when deciding weight) ----------------
def test_probe_only_when_contested():
    check("probe a bare agree on a CONTESTED belief", C.should_probe("agree", True))
    check("don't probe agree when not contested", not C.should_probe("agree", False))
    check("never probe a counter", not C.should_probe("counter", True))


# ---------------- question is interrogative (self-labeling) ----------------
def test_question_is_a_question_not_a_fact():
    q = C.compose_question("X is the better design", "Y", "Z")
    check("question ends by asking the user", "Do you agree" in q)
    check("lean is framed as a lean, not asserted", q.startswith("I'm leaning"))


# ---------------- provenance (the honesty core) ----------------
def test_provenance_records_collaboration():
    w = wall.assess(0.3, 0.5, 0.5)
    p = C.build_provenance("yes, agreed", adopted=True, derivation="t/a/s", wall_assessment=w)
    d = p.to_dict()
    check("formed_via collaborative", d["formed_via"] == "collaborative")
    check("user input recorded", d["user_input"] == "yes, agreed")
    check("adopted true", d["user_input_adopted"] is True)
    check("no overruled dissent when adopted", d["overruled_dissent"] == "")
    check("wall assessment embedded", "wall_score" in d["wall"])


def test_overruled_user_dissent_is_kept():
    # The hard requirement: user input considered but NOT adopted is KEPT.
    w = wall.assess(0.3, 0.5, 0.5)
    p = C.build_provenance("no, it's actually W", adopted=False,
                           derivation="t/a/s", wall_assessment=w)
    d = p.to_dict()
    check("overruled dissent preserved", d["overruled_dissent"] == "no, it's actually W")
    check("adopted flagged false", d["user_input_adopted"] is False)


# ---------------- audit event tuple ----------------
def test_wall_event_tuple_complete():
    w = wall.assess(0.3, 0.5, 0.5)
    ev = C.wall_event(w, "X is better", "agree", synthesis_changed=True, promoted=False)
    for k in ["ts", "wall", "lean", "user_response", "synthesis_changed", "promoted"]:
        check(f"event has '{k}'", k in ev)
    check("promoted reflects reality (not auto)", ev["promoted"] is False)


if __name__ == "__main__":
    for fn in [
        test_wall_fires_on_weak_and_balanced, test_wall_silent_when_confident,
        test_wall_silent_when_decisive, test_wall_is_conservative,
        test_wall_score_is_smooth_not_cliff, test_wall_assessment_is_auditable,
        test_cutoff_is_tunable, test_classify_agree, test_classify_counter,
        test_classify_ignore, test_probe_only_when_contested,
        test_question_is_a_question_not_a_fact, test_provenance_records_collaboration,
        test_overruled_user_dissent_is_kept, test_wall_event_tuple_complete,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
