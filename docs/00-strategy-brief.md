# Strategy brief — the argument, not the numbers

Numbers live in [`01-data-analysis.md`](01-data-analysis.md); every one is reproducible
via `uv run analytics/mis.py findings`. This file is the case we make.

## Verdict

**Feasible.** Joins resolve (99.1–100%), there are real anomalies, one real incident
with a derivable root cause, and enough cost, safety and emissions signal to give every
metric context.

**Change:** one pipeline with three outputs, not four products. A chat agent, an
alerter, a report writer and a dashboard is four surfaces to build badly.

**Cut:** the feedback table. Ratings vary by 0.015 of a point across the whole quarter.

## The three things worth building the pitch on

**1. The platform's own punctuality field is wrong by 25 points.**
`delay_reason` says 90.2% on-time; recomputed from epochs it is 64.9%. `delay_minutes`
correlates 0.04 with actual departure slip. Every team will bind a dashboard to that
field. An agent that recomputes ground truth and contradicts its own source of record
is the thesis: *dashboards report fields; the agent reports reality.*

**2. There is a real incident with a causal chain and a control period.**
Punctuality: 70.5% → 56.0% (week of Jun 1) → 69.8%. Cause: `pinnacle-Slc` demand +17.1%
against a fleet that grew 0.1%. July is the control — volume flattens, punctuality
recovers on a *smaller* fleet, so the recovery is re-planning, not capacity. That is
sense → reason with a quantified driver and a testable counterfactual.

**3. The worst vendor in the fleet is invisible to every dashboard.**
Pooja Sokolov Travel: 556 trips (0.09% of volume), 21.9% on-time, 246.7 min mean delay,
bad every week for three months. It never enters a top-N table, never moves an average,
never trips a threshold. "Surface what matters, not what is large" has a concrete
instance in this data.

## Persona coverage

| Persona | Grain | Verdict |
|---|---|---|
| Transport Manager | trip × vendor × office, 6,690/day | Fully served |
| Transport & Facilities Head | business unit × billing cycle | Fully served — this is where the bonus lives |
| Team / Line Manager | **no team column exists** | Served by proxy: `office × shift_type`, 484 real units |

Say the proxy out loud on stage. "There is no team hierarchy in this dataset, so we
define a team as everyone on a shift at a site. In production this binds to your HRIS."
Eight seconds, and it converts the weakest persona into evidence you read the data.

## Architecture call

- **Sense** — replay the 92 days on a clock, detectors evaluating against a rolling
  baseline, not the full-quarter aggregate. The agent flags June on June 3rd,
  unprompted. That is what makes autonomy watchable rather than claimed.
- **Reason** — no detection ships without four attached pieces of context: trend
  (own baseline), peer (comparable entity), threshold (SLA/policy), impact (₹, minutes,
  people). Enforce it structurally and "every metric must provide context" is satisfied
  by construction.
- **Act** — an action is an object with an owner, a deadline and a value. Three thin
  surfaces: alert feed + chat (manager), generated leadership memo (head), team
  readiness view (line manager).

## Where the differentiation actually is

The chat window is table stakes — assume every team ships one. The separation is:

1. **The agent contradicts its own source of record.** Nobody else questions the field they were handed.
2. **The agent reasons causally with a counterfactual**, not "the metric moved."
3. **The agent declines to report noise.** Saying "ratings show no discriminating signal this quarter, so this rests on operational evidence" demonstrates judgement — the hardest thing to fake.

See [`02-agent-design.md`](02-agent-design.md) for memory and evaluation design.
