# Corrigibility in Aida

Corrigibility here means:

> the maintained capacity for consequential correction
> to alter future behavior.

It does not mean Aida is always correct.

## Two core distinctions

### Knowing is not changing

A critique only matters operationally if it can affect persistent state.

When a material correction arrives, Aida records whether it actually
changed durable memory (`signal_received` vs `state_changed`). That is
an audit trail, not a score.

### The step is not the trajectory

Small individually-reasonable changes can cumulatively create a state
none of them would have justified alone.

Trajectory review is gated. It does not run on ordinary chat. It
activates for capability-surface expansion (`:enable` on, `:allow` add)
and for autonomy-shaped durable beliefs. The first step in a direction
is recorded quietly. A later step in the same direction surfaces one
question:

> Would this still make sense if all of these steps were viewed as one trajectory?

It does not auto-block.

## Mechanisms

- surviving dissent (already in deliberation)
- correction envelopes (optional: basis, boundary, disconfirmers, affected_by, trajectory, last_challenged)
- explicit disconfirmers (one reopen condition at synthesis — same model call, not an extra round)
- visible model boundaries
- correction → state-change audit
- gated trajectory review
- optionality notes only where a gated step reduces future alternatives

Inspect with `:why` / `:belief <id>`. `:why trajectory` lists recorded steps.

## Scope

These mechanisms increase inspectability and correctability.
They do not guarantee correctness, complete threat coverage,
or resistance to every unknown failure mode.
The frame itself remains finite.

User-stated facts and corrections stay authoritative. The envelope
applies to model-derived durable beliefs and consequential
recommendations, not to the persona layer.

Empty reopen conditions are stored as empty. Vague boilerplate
("new evidence", "if I'm wrong") is dropped rather than kept.
Honest absence is better than fake corrigibility.
