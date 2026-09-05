# Status — integration check

Verified against a full `make replay` over all 92 days, plus every endpoint, both test
suites and the Angular build. Ordered by what would hurt on stage.

---

## What works

| | |
|---|---|
| Frozen layer | untouched — `git log` shows one commit against every frozen file |
| Service discovery | all three mount automatically; 21 routes, all returning 200 |
| **Contract drift** | **zero** — every one of the frontend's 20 API calls matches a real route |
| Replay | 92 days, 615,546 trips, 4m22s, exit 0 |
| Idempotency | re-running a date range leaves row counts unchanged |
| Trace completeness | 128/128 incidents carry all four context blocks |
| Refuted hypotheses | every incident has at least one |
| Angular dev server | boots clean, 297 kB bundle, `<app-root>` renders |
| Tests | 42 passing |

**The three hero cases are all detected:**

- **21 July, Cedar Ridge** — one site incident, four vendor signals folded in with
  `child_of_parent` and the explanation *"5 of 5 vendors fell into a 7-point band
  (56.0% to 63.0%), so this is not a vendor failure…"*
- **20 July, Santa Clara** — `metric_integrity` fires with the headline *"Santa Clara
  on-time rose 16.5 points while actual journey time did not change"*
- **Cedar Ridge case file** — 7 occurrences, promoted to `structural`

The probe suite is the strongest thing in the repo. Twelve real probes with observed
values, and the three that matter most all pass: `fake_improvement`,
`alert_type_deleted`, `sunday_composition`. The faithfulness gate is better than the
spec asked for — its number regex already excludes the `-1` in `Sev-1` via lookbehind,
and it resolves an incident's evidence pool by unioning its signals' evidence with the
context leaves.

---

## P1 — fix before the demo

### 1. The report card shows three of four gates failing · **C**

It is on screen for the entire demo, and it currently reads:

```
faithfulness  0.0142 unsourced          FAIL
detection     P 0.036  R 0.111          FAIL
trace_schema  1.000                     pass
behaviour     9/12                      FAIL
```

Detection is the urgent one. Precision 0.036 against 128 incidents means the ground-truth
definition and the flag set are not comparing the same population — most likely the
flagged set includes every incident across all detectors while ground truth only covers
punctuality. Scope both sides to the same entity type and metric.

`median_lead_days = 81` on a 92-day window is degenerate — it is measuring from day one.
A judge will poke exactly this number.

### 2. Duplicate signals land on the hero cases · **A**

Seven duplicate groups, and they sit exactly where the demo looks:

| Date | Entity | Emitted |
|---|---|---|
| 21 Jul | Cedar Ridge Office | z −4.18 **and** −4.14 |
| 21 Jul | Sneha Mikhailov Travel | z −6.45 **and** −4.44 |
| 21 Jul | Meera Pavlov Travel | z −4.01 **and** −3.88 |
| 20 Jul | Santa Clara `metric_integrity` | 52.3/35.8 **and** 49.7/35.5 |

Two near-identical cards during the money shot. The differing `z` and `n` suggest two
baseline paths for one entity-metric. Only 7 rows fleet-wide, so it should be a small fix.

### 3. Unrounded floats in 115 of 128 narratives · **B**

Text like *"actual journey time changed by only 0 minutes to 57.0552 minutes"* is on
screen. Round at format time — one helper, applied in `narrate.py`.

### 4. A `target_moved` suppression reads as nonsense · **B**

> "Planned duration rose 0%; actual journey time changed by only 0 minutes to 57.0552 minutes. Improvement rejected."

A 0% rise should not trigger `target_moved` at all. Add a minimum-delta guard, and fix
the formatting while you are there.

### 5. `make ui` runs in mock mode · **D**

`environment.ts` (dev) has `useMock: true`, so `make ui` shows fixtures rather than the
real 92-day replay. Correct for building; wrong for demoing. Add a visible mode badge and
decide deliberately which way the flag points on the day.

---

## P2 — worth fixing if there is time

**6. The reasoning stage raised once** · **B** — `2026-06-22: ValueError: incident
bca0c7fcbe1ab379 requires a refuted hypothesis`. The guard is right; raising is not.
Degrade to `inconclusive` and log, so one day cannot lose its reasoning.

**7. `metric_integrity` over-fires** · **A** — 9 firings in July, several of them ordinary
recoveries (Vikram Mikhailov Travel on 4 July, Fairview Commons three times). Only Santa
Clara is real schedule padding. Tighten the "actual duration unchanged" test; the
generic *"rose to X% from Y% because…"* headline also reads weaker than the Santa Clara one.

**8. `noshow_spike` is the largest July incident source (16 of 37)** · **A/B** — and probe
2 fails with *9 uncaveated claims*. This is precisely the trap
[`docs/01-data-analysis.md`](01-data-analysis.md) flags: the 16× office spread is a
recording artifact of null `signintype`, not behaviour. Either attach the caveat to every
no-show incident or stop raising them at office grain.

**9. Two more probes fail** · **A/B** — `small_sample_abstain` (3 small signals not held)
and `sev1_spike` (no narrative names catalyst-Sac's 993-minute acknowledgement time).
Both are one-line fixes in the narrative and suppression paths, and both are probes a
judge might well ask about.

**10. Case matching is too loose** · **C** — 66 case files with one at **58 occurrences**.
`known_pattern` is now the largest suppression category (60 in July), and one explanation
reads *"already has 49 occurrences"*, which sounds broken even though it is honest.
Cap or bucket the signature.

**11. July funnel is 144 → 109 → 37** — against the ~84 → 74 → 10 the research predicted.
37 incidents across 31 days is noisy for a four-minute demo. Items 2, 7 and 8 together
account for most of the excess; fixing them should bring it close.

**12. `ng build --configuration production` fails** · **D** — Google Fonts inlining needs
internet: *"Inlining of fonts failed."* Irrelevant if you demo with `ng serve`, fatal if
you build on venue wifi. Set `optimization.fonts.inline: false` in `angular.json`, or
self-host the two families.

**13. `eval_result` gets a run per replay day** · **C** — 14+ `run_id`s. The API picks the
latest correctly, so this is cosmetic, but the table will keep growing.

---

## Fixed during this check

Both were mine, in the frozen layer.

- **`make install` failed on any re-run** — `uv venv` refuses an existing `.venv`. Now
  passes `--allow-existing`.
- **Two fixture headlines contained unsourced numbers** — `signal.json` claimed *"rose
  16.5 points"* and *"against a 7.2 daily baseline"* with neither value in its own
  `evidence` array. A's test caught it, which is exactly what that test is for.

## One spec gap I should own

`CONTRACT.md` §8 says every narrative number must trace to "the accompanying evidence
array", but the `incident` table has no `evidence` column — only `context` and
`signal_ids`. C resolved it sensibly by unioning the referenced signals' evidence with
the context leaves. The contract should have said so. Worth a one-line amendment rather
than leaving it implicit.

---

## Suggested order of work

| Owner | Do first |
|---|---|
| **C** | Detection gate scoping, then `median_lead_days` — the report card is always on screen |
| **A** | De-duplicate signals (P1 #2), then tighten `metric_integrity` |
| **B** | Round the floats, guard `target_moved`, stop raising on a missing refutation |
| **D** | Decide the mock flag and add a mode badge; fix the production font build if you need it |

Nothing here is architectural. The contract held across four parallel streams with zero
interface drift, which is the part that would have been expensive to get wrong.
