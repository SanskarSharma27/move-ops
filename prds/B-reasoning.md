# PRD B — Reasoning

**You own `backend/services/reason/` and nothing else.**

You are building the part that decides what deserves a human's attention, and — more
importantly — **what does not, and why**. In July the sensing layer raises about 84 raw
signals. Ten should survive. The seventy-four you discard, each with a reason a
transport manager would accept, are the most convincing thing in this product.

Anyone can build a detector. Almost nobody builds the thing that explains what it
ignored.

---

## 0. Read these first, in this order

1. **`CONTRACT.md`** — ownership, helpers, vocabulary, commit rules. Non-negotiable.
2. **`contracts/schema.sql`** — DDL for `incident`, `suppression`, `hypothesis`.
3. **`contracts/api.md`**, `/api/reason` section.
4. **`docs/02-demo-cases.md`** — read the suppression ledger twice. It is your spec.
5. **`docs/01-data-analysis.md`** — the data underneath.

## 1. Setup

```bash
make install
make build      # once, ~40s
make seed       # loads signal + counterfactual fixtures — you can start immediately
make api
```

**You are not blocked on A.** `make seed` puts eight real `signal` rows and five
`counterfactual` rows in the database, including the whole 21 July cluster. Build
against those. When A lands, your code sees hundreds instead of eight and nothing
changes.

## 2. Commit discipline — mandatory

**Commit every 30–45 minutes, and always before starting a new file.**

```bash
git add backend/services/reason
git commit -m "reason: fold child vendor signals into the parent site incident"
git pull --rebase origin main
git push origin main
```

- Never `git add .` or `git add -A`. Stage your folder by name.
- Prefix every message `reason:`.
- Never push a broken import — the router auto-discovery imports your package.
- If a frozen file looks wrong, **say so in the group chat**. Do not edit it.

## 3. What you build

```
backend/services/reason/
├── __init__.py         exports run_day
├── pipeline.py         run_day orchestration
├── correlate.py        the suppression ledger — your headline feature
├── hypotheses.py       competing explanations, tested and eliminated
├── narrate.py          the paragraph a human reads, plus the recommendation
├── router.py           four GETs and one POST
├── requirements.txt
└── tests/test_reason.py
```

You write `incident`, `suppression`, `hypothesis`. You read `signal`,
`entity_baseline`, `counterfactual`, `field_trust`, `mis.*`. You never recompute a
metric — if you need a projection, read `counterfactual`.

## 4. Build order

### Hour 0–4 · `correlate.py` — build this first

Take a day's signals and split them into incidents and suppressions. Five reason codes,
applied in this order:

**1. `composition`** — the metric moved because the mix changed.
Weekend days carry 375–1,100 trips against a 9,700 weekday norm. Fleet on-time is 96%
every Sunday and 60% on Tuesdays. 56 of 84 July signals are weekend artifacts.

> "Sunday runs 382 trips against a 9,700 weekday norm and a different trip mix. Fleet
> on-time is 96% every Sunday and 60% on Tuesdays. The mix changed, the performance
> did not."

**2. `small_sample`** — n < 40. 58 of 84 signals, at a mean |z| of 2.56.

> "33 trips is below the 40-trip floor at which a z of −2.02 is distinguishable from
> ordinary variance for this vendor. Held, not escalated."

**3. `child_of_parent`** — the important one. When several entities sharing a
`parent_id` degrade on the same day, raise **one** incident on the parent and suppress
the children.

21 July is the canonical case. A naive detector fires two vendor alerts (Sneha
z = −4.44, Meera Pavlov z = −4.01). But all five vendors at Cedar Ridge were hit —
63.0, 56.0, 56.7, 60.9, 56.8 — a 7-point band. It is one site event.

Rule: if ≥ 60% of an entity's children fire the same detector on the same day, or the
parent itself fires, fold the children in. Set `parent_incident_id`.

> "Folded into the Cedar Ridge site incident. All five vendors at the site fell into a
> 7-point band that day (56.0% to 63.0%), so this is not a Sneha Mikhailov Travel
> failure and no vendor penalty is warranted."

That last clause is what a transport manager actually needs.

**4. `known_pattern`** — the signature already has an open case with ≥ 3 occurrences.
This is a **reclassification, not a dismissal.** Cedar Ridge at 60.5% on 29 July is not
fine, it is chronic. Update the existing incident to `status = 'structural'` and change
the recommendation from *investigate* to *fix the Tuesday capacity plan*.

Read `case_file` if it exists; degrade gracefully to occurrence-counting over `incident`
if C has not landed yet. **Never import from `services.memory`.**

**5. `target_moved`** — a `direction = 'better'` signal that `metric_integrity` flagged.
Suppress the *improvement*, raise an incident about the padding.

> "Planned duration rose 43%; actual journey time unchanged at 77 minutes. Improvement
> rejected."

Every `explanation` must contain the number that justifies it. The faithfulness gate
checks it.

### Hour 4–7 · `hypotheses.py`

Every surviving incident needs **≥ 2 hypotheses with ≥ 1 refuted**, or C's trace gate
fails it. Use a fixed library of parameterised SQL — do not generate SQL freely, it
will fail live on stage.

Six hypotheses, each a template that plugs in entity and date:

| Name | Refuted when |
|---|---|
| `vendor_failure` | all vendors at the site moved together |
| `systemic_day_event` | only this entity moved; peers were flat |
| `demand_surge` | trip volume flat against the trailing window |
| `capacity_shortfall` | cab count and trips-per-cab unchanged |
| `schedule_lag` | *supported* when actual min/km rose faster than planned min/km |
| `composition_shift` | fuel/capacity mix change explains nothing (needs a control group) |

The demo-grade example — vanta-Aus is shifting to EVs (27% → 35%) while punctuality
falls (89.5% → 81.5%). Obvious hypothesis, and it is wrong: Diesel goes 89.5 → 81.7 and
Electric 89.3 → 81.1, in lockstep. Control at orbit-Slc shows Electric 72.5% against
Diesel 71.7%. **Refuted, with a control group.** Store the SQL you ran in `test_sql`
and the numbers in `result`.

Write `reasoning` as one sentence tying result to verdict. That sentence is what
appears on screen with a strikethrough.

### Hour 7–10 · `narrate.py`

Assemble `incident.narrative`, `incident.context`, `incident.recommendation`.

**Templates, not free generation.** No LLM is required anywhere in this service, and
not using one is a selling point — every number is placed by code that read it from a
row, so it cannot be wrong.

`context` needs all four keys populated or the trace gate fails you:

```json
{"trend":     {"statement": "...", "values": {}, "unit": "%"},
 "peer":      {"statement": "...", "peer_group": "...", "peer_median": 85.1, "pctile": 2},
 "threshold": {"statement": "...", "target": 80.0, "actual": 59.3, "unit": "%"},
 "impact":    {"statement": "...", "value": 398, "unit": "employees"}}
```

`trend` and `peer` come straight out of `entity_baseline` — A has already computed
`slope_28d`, `peer_median`, `peer_pctile`. Do not recompute them.

**Impact must be rupees, minutes, or people.** Never a z-score in a sentence a human
reads. For punctuality, count the employees on affected trips from `mis.emp_legs`.

`recommendation` reads its `expected_effect` from `counterfactual` and **must carry the
assumption verbatim**. If the lever is `schedule_pad_min`, the honest caveat travels
with it: this moves the metric, not the commute.

Set `persona`: operational and vendor issues → `transport_manager`; spend, safety
governance, ESG → `transport_head`; no-show and rider lateness → `line_manager`.

### Hour 10–12 · `router.py`

Four GETs plus `POST /whatif`, shapes exactly as `contracts/api.md`.

`GET /summary` is the funnel the UI puts in its top strip — raw, suppressed, incidents,
and a `by_reason` breakdown, for both the day and the whole window.

`POST /whatif` takes `{incident_id, lever, param}` and returns a `counterfactual`
row plus a narrative. **It must answer sensibly when the lever is a bad idea** — that is
the interactive Q&A moment. Substituting Rohan (41.3%) for Vikram (43.2%) at Santa Clara
makes things marginally worse, and the correct response says so rather than erroring.
If the requested combination is not in `counterfactual`, return the closest available
with a note; never 500.

## 5. Traps

- **Suppressed ≠ deleted.** Every suppression keeps its `signal_id` and is served by
  the API. The UI shows all 74.
- Order matters: `composition` → `small_sample` → `child_of_parent` → `known_pattern`
  → `target_moved`. A weekend small-sample signal is a composition artifact first.
- `parent_id` on office rows is the business unit. Office `entity_id` is always
  `"business_unit / office"` — Cedar Ridge exists under two BUs with a 14-point gap.
- Never write to `signal`, `case_file`, `prediction`, `playbook`, or `eval_result`.
- Incident ids from `make_id(opened_on, detector, entity_type, entity_id)`. Re-running
  a day must not duplicate.
- If `case_file` is missing (C not landed), count occurrences over `incident` instead.
  Import nothing from another service package, ever.

## 6. Done when

- [ ] `make replay --only reason` runs clean over July after A, or over fixtures before
- [ ] Re-running produces identical row counts
- [ ] July: ~84 signals in, ~10 incidents out, ~74 suppressions, each with a reason
- [ ] Zero Sunday signals survive; each suppressed with `composition`
- [ ] 21 July yields **one** Cedar Ridge incident with both vendor signals folded in,
      `parent_incident_id` set, and "no vendor penalty is warranted" in the explanation
- [ ] Santa Clara 19–20 July raises a `target_moved` incident rejecting the improvement
- [ ] Every incident has ≥ 2 hypotheses, ≥ 1 refuted
- [ ] Every incident has all four `context` keys with a numeric `impact`
- [ ] Every number in every `narrative` and `explanation` appears in its `evidence`
- [ ] `POST /whatif` returns a considered answer for a bad substitution
- [ ] `make test` passes; everything committed and pushed

## 7. Tests

`tests/test_reason.py`, pytest. Cover:

1. Five vendor signals sharing a parent on one day → one incident, four suppressions
2. A Sunday signal is suppressed with `composition`
3. n = 33 is suppressed with `small_sample`
4. A `direction='better'` metric_integrity signal produces a `target_moved` incident
5. Every incident produced has all four context keys populated
6. Every numeral in a narrative is present in that incident's evidence array
7. `run_day` twice for one date leaves row counts unchanged
