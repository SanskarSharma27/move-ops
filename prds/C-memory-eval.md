# PRD C — Memory & Evaluation

**You own `backend/services/memory/` and nothing else.**

Two jobs that are really one job. **Memory** records what the agent concluded and later
checks whether it was right. **Evaluation** scores the agent against outcomes it could
not see. Both are the same operation — compare a claim to what actually happened — which
is why one person owns both instead of two people building it twice.

Your output is the credibility of the whole project. When a judge asks "how do you know
it isn't making this up," the answer is a number you computed.

---

## 0. Read these first, in this order

1. **`CONTRACT.md`** — ownership, helpers, vocabulary, commit rules. Non-negotiable.
2. **`contracts/schema.sql`** — DDL for your four tables.
3. **`contracts/api.md`**, `/api/memory` section.
4. **`docs/02-demo-cases.md`** — especially §"Can the agent propose solutions".
5. **`docs/01-data-analysis.md`** — the ground truth your probes assert against.

## 1. Setup

```bash
make install
make build      # once, ~40s
make seed       # loads incident + hypothesis fixtures — start immediately
make api
```

**You are not blocked on A or B.** `make seed` puts three real `incident` rows and four
`hypothesis` rows in the database, including the recurring Cedar Ridge case. Build
against those.

## 2. Commit discipline — mandatory

**Commit every 30–45 minutes, and always before starting a new file.**

```bash
git add backend/services/memory
git commit -m "memory: verify predictions against later replay days"
git pull --rebase origin main
git push origin main
```

- Never `git add .` or `git add -A`. Stage your folder by name.
- Prefix every message `memory:`.
- Never push a broken import.
- If a frozen file looks wrong, **say so in the group chat**. Do not edit it.

## 3. What you build

```
backend/services/memory/
├── __init__.py       exports run_day
├── pipeline.py       run_day orchestration
├── cases.py          open, match, update case files
├── verify.py         check predictions against what actually happened
├── playbook.py       promotion — the only way a playbook entry is created
├── evaluate.py       the four gates
├── probes.py         the twelve behavioural probes
├── router.py         six GET endpoints
├── requirements.txt
└── tests/test_memory.py
```

You write `case_file`, `prediction`, `playbook`, `eval_result`. You read `incident`,
`hypothesis`, `signal`, `suppression`, `entity_baseline`, `mis.*`.

## 4. The central design decision — read this before writing code

**This dataset contains no interventions.** Vendor mix is frozen across all three
months (Vikram holds 48.9% of Santa Clara in both May and July); no vendor–office pair
starts or stops mid-window; fleet size is flat. Nobody ever acted on anything.

So the agent **cannot** learn "action X worked." There is no X.

What it can learn is whether its own **diagnoses** held up. Every case carries a
falsifiable prediction with a date. Later replay days check it. Memory is earned by
predictive accuracy, not by action outcomes.

This is more honest than the alternative and it demos better — the agent can say *"I
predicted this on 8 July and I was right on 21 July"*, and it can equally say *"I was
wrong."* Never claim a verified outcome for an action taken.

## 5. Build order

### Hour 0–3 · `cases.py`

A case is one recurring problem, not one occurrence. Retrieval key is
`signature = "detector|entity_type|entity_id"`.

On each replay day, for each new incident:

- **Signature match on an open case** → increment `occurrences`, append to
  `incident_ids`, bump `last_seen_on`, refine `diagnosis`.
- **No match** → open a new case.
- **Promote status** by occurrence count: 1 = `open`, 2–3 = `recurring`,
  ≥ 4 = `structural`.
- **Auto-resolve** when the entity has been within baseline for 14 consecutive days.

**No embeddings.** With ~14 detectors and ~50 entities a SQL `WHERE` on signature is
faster, exact, and explainable. A vector store here is pure overhead — do not add one.

`diagnosis` is the current best explanation, rewritten as evidence accumulates. It is
displayed in the UI, so write it as prose a transport manager would read. The Cedar
Ridge case in the fixtures shows the register.

### Hour 3–6 · `verify.py` — the heart of it

When a case opens or updates, attach a **falsifiable prediction**:

```json
{"statement": "Cedar Ridge will fall below 80% on-time again within seven days, on a Tuesday or Wednesday.",
 "metric": "ota15", "predicate": "lt", "threshold": 80.0,
 "made_on": "2026-07-08", "verify_on": "2026-07-15"}
```

On every replay day, pick up predictions where `verify_on <= day` and `outcome is null`.
Read the actual value from `mis.*` or `entity_baseline`, compare against the predicate,
and write `outcome` = `confirmed` | `refuted` | `unverifiable` (use `unverifiable` when
the entity had no qualifying trips in the window — do not guess).

**Record refutations honestly.** An agent that shows a wrong prediction is dramatically
more credible than one that shows only wins. If your July run produces zero refutations,
your predictions are too weak — tighten the thresholds.

The real arc, already in the data: Cedar Ridge fires 8 July (72.9%), 15 July (77.8%),
21 July (59.3%), 28 July (67.0%), 29 July (60.5%). A prediction made on 8 July that it
will breach 80% within seven days is **confirmed on 15 July**, and again on 21 and 28.

### Hour 6–8 · `playbook.py`

Procedural memory. **An entry is created only by promotion. Never by assertion.**

Rule: an action enters the playbook when **≥ 2 predictions confirmed for the same
signature**. `confidence = n_confirmed / n_cases`. Recompute on every promotion.

This is the visible learning: the playbook table is **empty on 10 July and populated on
20 July**. Make sure `promoted_on` is set correctly so the UI can show it filling up.

### Hour 8–12 · `evaluate.py` and `probes.py`

Four gates. **Every one deterministic. No LLM anywhere in this service** — that is a
deliberate design choice and a strong answer in Q&A.

**Gate 1 — `faithfulness`.** Mechanical hallucination check, and the highest
value-per-hour thing you will build.

Extract every numeral from `incident.narrative`, `incident.headline`, and
`suppression.explanation` — regex covering integers, decimals, comma-grouped thousands,
percentages, currency. Each must match a `value` in the accompanying `evidence` array
within rounding tolerance (±0.05 for one decimal place, exact for integers). Report
`unsourced_number_rate`. **Target is 0.0.**

Ignore years (2026), dates, and ordinals — maintain a small exclusion list.

**Gate 2 — `detection`.** Held-out scoring. Memory warms up on May–June; July is scored.

Ground truth, computed from July data only so it is independent: an entity is
"degraded in July" if its July `ota15` sits more than 1 sd below its peer-group median.
Score precision, recall, F1 against the entities the agent flagged.

**`median_lead_days` is the headline enterprise metric.** For each true positive,
measure days between the agent's first flag and the earliest date a month-end report
(consecutive months, >5 point drop) could have caught the same entity. Report the
median. Expect a strong number — Pooja Sokolov Travel was bad from week one and a
monthly report cannot see it until 30 June.

**Gate 3 — `trace_schema`.** Structural assertions on the incident object. No judgement,
no model — just property checks:

- ≥ 2 hypotheses attached
- ≥ 1 hypothesis with `verdict = 'refuted'` and a non-empty `result`
- all four `context` keys present and populated
- `recommendation` has `action`, `owner`, `due`
- `context.impact.value` is numeric and its unit is money, minutes, or people

Report the fraction of incidents that pass.

**Gate 4 — `behaviour`.** Twelve fixed probes with known-correct answers taken from
`docs/01-data-analysis.md`. Each asserts against the database, not against generated
text:

| # | Probe | Correct behaviour |
|---|---|---|
| 1 | Fleet on-time rate | 64.9%, recomputed — never 90.2% |
| 2 | Rank offices by no-show | refuses or caveats: null `signintype` recording artifact |
| 3 | Rider satisfaction trend | no discriminating signal, 0.015 spread over 14 weeks |
| 4 | Is Pooja Sokolov Travel underperforming | yes — 21.9% on-time, n = 556, flagged consistent |
| 5 | A vendor with 3 trips looks bad — act? | abstain, n below floor |
| 6 | Sev-1 spike on 15 July | escalate, and name catalyst-Sac's 993-minute ack time |
| 7 | Show marshal ratings | refuse — quarantined field, 92.4% placeholder |
| 8 | Total spend | ₹834M, with the 45.4% zero-km caveat attached |
| 9 | Did Santa Clara improve on 19 July | no — schedule padding, actual journey unchanged |
| 10 | Did pinnacle-Slc fix alert response | no — alert type deleted; like-for-like 562 → 439 |
| 11 | Sunday on-time is 96% — celebrate? | no — composition artifact, 382 trips |
| 12 | Cheapest four-seater contract | `4Seater-LVT-July` at ₹49.42/km vs `4Seater` at ₹82.90 |

Report pass count. Any failure names the probe.

Write everything to `eval_result` and expose `GET /report-card` in the shape given in
`contracts/api.md`. **That endpoint is on screen for the entire demo — get its shape
exactly right.**

## 6. Traps

- Prediction ids from `make_id(case_id, made_on, metric)` — re-runnable.
- Never write `incident`, `signal`, or `suppression`.
- Never import from `services.reason` or `services.sense`. Read their tables.
- Ground truth for gate 2 must use **July data only**. Deriving it from the full window
  leaks the answer and the number becomes meaningless.
- `unverifiable` is a real outcome. Use it rather than guessing.
- Currency is unstated in the dataset; `₹` is assumed. Probe 8 must carry that caveat.

## 7. Done when

- [ ] `make replay --only memory` runs clean over July, after B or over fixtures
- [ ] Re-running produces identical row counts
- [ ] Cedar Ridge accumulates one case reaching `structural` with ≥ 4 occurrences
- [ ] ≥ 3 predictions verified, and at least one **refuted** — honesty over a clean sheet
- [ ] `playbook` is empty at 10 July and non-empty at 20 July
- [ ] No playbook entry exists without ≥ 2 confirmed predictions behind it
- [ ] `faithfulness.unsourced_number_rate` = 0.0, over ≥ 500 statements
- [ ] `detection` reports precision, recall and `median_lead_days`
- [ ] `trace_schema` reports the fraction of complete incidents
- [ ] 12/12 behavioural probes pass, or the failures are named and understood
- [ ] `GET /report-card` matches `contracts/api.md` exactly
- [ ] `make test` passes; everything committed and pushed

## 8. Tests

`tests/test_memory.py`, pytest. Cover:

1. Two incidents with the same signature produce one case with `occurrences = 2`
2. A prediction whose threshold is breached is marked `confirmed`; one that is not is `refuted`
3. A playbook entry is **not** created with only one confirmed prediction
4. Faithfulness flags a narrative containing a number absent from its evidence
5. Faithfulness ignores the year 2026 and ISO dates
6. `trace_schema` fails an incident missing the `peer` context key
7. All twelve probes run and report a pass/fail each
8. `run_day` twice for one date leaves row counts unchanged
