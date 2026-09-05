# PRD A — Sensing

**You own `backend/services/sense/` and nothing else.**

You are building the layer that watches the data and decides what is worth mentioning.
Everything downstream — every incident, every case, every number in the leadership
memo — is derived from rows you write. If your baselines are wrong, three other people
build on sand.

---

## 0. Read these first, in this order

1. **`CONTRACT.md`** — ownership, helpers, vocabulary, commit rules. Non-negotiable.
2. **`contracts/schema.sql`** — the DDL for your four tables.
3. **`contracts/api.md`**, `/api/sense` section — your four endpoints.
4. **`docs/01-data-analysis.md`** — what is in the data.
5. **`docs/02-demo-cases.md`** — the exact cases your detectors must catch.

## 1. Setup

```bash
make install
make build      # analytics/mis.duckdb from the CSVs — once, ~40s
make seed       # fixtures so nothing is empty
make api        # http://localhost:8000/docs
```

Explore before you write anything:

```bash
uv run analytics/mis.py shell
uv run analytics/mis.py findings F-HOOK
```

## 2. Commit discipline — mandatory

**Commit every 30–45 minutes, and always before starting a new file.**

```bash
git add backend/services/sense
git commit -m "sense: weekday-aware baselines for vendor and office"
git pull --rebase origin main
git push origin main
```

- Never `git add .` or `git add -A`. Stage your folder by name.
- Prefix every message `sense:`.
- Never push a file with a syntax error — router auto-discovery imports your package,
  and a broken import degrades everyone's API.
- If you believe a frozen file is wrong, **say so in the group chat**. Do not edit it.

## 3. What you build

```
backend/services/sense/
├── __init__.py          exports run_day
├── pipeline.py          run_day orchestration
├── field_trust.py       audit each source column, once
├── baselines.py         weekday-aware rolling context
├── detectors.py         the seven detectors
├── counterfactual.py    precomputed what-if grid
├── router.py            four GET endpoints
├── requirements.txt     your own extra deps (probably none)
└── tests/test_sense.py
```

You write `field_trust`, `entity_baseline`, `signal`, `counterfactual`. You read
`mis.*`. You never write another service's tables.

## 4. Build order

### Hour 0–2 · `field_trust.py`

Audit each source column and decide what it is worth. This runs once, not per day.
Call it from `run_day` guarded on the first replay date.

Verdicts: `trusted` | `degraded` | `quarantined`. Write at least these eight rows —
they are already computed in `docs/01-data-analysis.md`, so this is mostly encoding
known answers as tests that recompute them:

| Column | Verdict | Test |
|---|---|---|
| `trips.delay_minutes` | quarantined | correlation with reconstruction = 0.04, zero on 90.2% |
| `trips.delay_reason` | quarantined | claims 90.2% on-time, timestamps say 64.9% |
| `feedback.marshal_rating` | quarantined | 92.4% zero-as-placeholder |
| `feedback.driver_rating` | degraded | spread 0.015 across 14 weeks — no discriminating power |
| `alerts.severity` | degraded | 15,037 rows hold the string `'False'` |
| `bills.total_trip_km` | degraded | zero on 39.97% of lines carrying 45.4% of spend |
| `emp_legs.signintype` | **trusted** | null means "never picked up" — 62.1% no-show. A signal, not a defect |
| `trips.actual_end_epoch` | trusted | complete and consistent — the punctuality source of record |

**Recompute each number from `mis.*`; do not hardcode.** The evidence sentence must
contain the number, because the faithfulness gate will check it.

Expose a helper other modules import:

```python
def is_quarantined(con, table: str, column: str) -> bool: ...
```

and **raise** if any detector tries to build a metric on a quarantined column. That
refusal is a demo moment — make it loud and make it logged.

### Hour 2–4 · `baselines.py`

One row per `(as_of, entity_type, entity_id, metric)` into `entity_baseline`.

**The baseline is trailing 28 days, same weekday only.** This is the single most
important line in this PRD. Fleet on-time is 96% on Sundays and 60% on Tuesdays; a
naive trailing mean fires a false positive every Sunday. Six of them sit in the data
already (7, 14, 28 June and 5, 12, 19 July).

```sql
window w as (
  partition by entity_type, entity_id, metric, dayofweek(trip_date)
  order by trip_date
  range between interval 28 days preceding and interval 1 day preceding
)
```

Entities: `vendor`, `office`, `business_unit`. Metrics: `ota15`, `ack_minutes`,
`noshow_pct`, `seat_util`, `sev1_count`.

Populate `parent_id` on every row — **vendor → business_unit**, **office →
business_unit**. B's correlation logic cannot work without it, and it is the thing
that turns two vendor alerts on 21 July into one site incident.

`entity_id` for an office is always `"business_unit / office"`. Cedar Ridge Office
exists under both `vanta-Aus` (85.1% on-time) and `orbit-Slc` (71.0%). Merging them
is a real bug that will produce nonsense.

Also fill `peer_group`, `peer_median`, `peer_pctile`, `slope_28d`. B reads these
directly to build the `context.peer` block, so if they are null B has nothing to say.

### Hour 4–8 · `detectors.py`

Emit `signal` rows. **Do not suppress anything** — B owns that. Your job is recall;
B's job is precision. A signal you never raise is one nobody can ever recover.

Seven detectors:

| Detector | Fires when | Verify against |
|---|---|---|
| `punctuality_drop` | `ota15` z < −2, any entity | 8 vendor + 5 office firings in July |
| `metric_integrity` | metric improves while its underlying quantity does not | Santa Clara, 19 July |
| `alert_ack_sla` | `ack_minutes` above SLA or far above peer | catalyst-Sac at 993 min |
| `safety_cluster` | daily Sev-1 count z > 2.5 | 5 days: Jul 8, 15, 16, 22, 30 |
| `escort_breach` | `WOMAN_TRAVELLING_ALONE` on a trip with `actual_escort = false` | every day, 69–98 trips |
| `noshow_spike` | `noshow_pct` z > 2.5, n ≥ 200 | Clearwater, Jul 29 and 31 |
| `billing_anomaly` | at cycle close: zero-km share, duplicate lines, contract cost-per-km outliers | Jul 31 |

**`metric_integrity` is the headline feature. Build it carefully.** It fires when a
metric moves in the *good* direction for a bad reason. Three patterns, all present:

1. **Schedule padding** — `ota15` up while `avg(actual_end − actual_start)` is flat.
   Compare planned-minutes-per-km against actual-minutes-per-km so trip composition
   cannot explain it away. Santa Clara: planned 40.6 → 58.1 min (+43%), actual
   76.4 → 77.0 min, on-time +16.5 points, 14 *fewer* cabs.
2. **Denominator change** — a rate improves because the population shrank.
3. **Category deletion** — an aggregate improves because a slow category stopped being
   recorded. pinnacle-Slc alert ack 1,215 → 439 min, entirely because
   `EMPLOYEE_SIGN_OFF_TIME_VIOLATION` (7,664 alerts at 1,444 min) vanishes in June.
   Like-for-like the gain is 562 → 439. Proof nothing was fixed:
   `DEVICE_NOT_REACHABLE` runs 1,444.4 → 1,444.8 → 1,444.9 across three months.

Set `direction = 'better'` on these and let `severity` stay high. A `better` signal
that is actually a defect is the most interesting row in the table.

Every signal needs a populated `evidence` array. Every number in `headline` must
appear there. See `CONTRACT.md` §8.

Signal ids come from `make_id(as_of, detector, entity_type, entity_id, metric)` so a
re-run overwrites rather than duplicates.

### Hour 8–10 · `counterfactual.py`

Precompute a grid into `counterfactual`. B reads it; B never recomputes metrics.

| Lever | Params | Confidence | Method |
|---|---|---|---|
| `schedule_pad_min` | 5, 10, 15 | `exact` | recount `is_ontime` against `planned_end + N min` on the same trips |
| `vendor_substitute` | each vendor at the site | `weak` if n < 200 else `estimated` | apply the candidate's observed site on-time to the incumbent's volume |
| `fleet_add` | computed deficit | `estimated` | trips ÷ target trips-per-cab, minus cabs run |

**`assumption` is mandatory and must be honest.** For `schedule_pad_min` it must say
this moves the metric and not the commute — it is the same trick Santa Clara used on
19 July. An agent that recommends schedule padding without that caveat has failed its
own metric-integrity test, and a judge will notice.

Known-good values to check yourself against: vanta-Aus July on-time is 81.5%; at +5 min
it is 86.7%; at +10 min it is 90.4%, over 23,584 trips.

### Hour 10–11 · `router.py`

Four GETs, shapes exactly as in `contracts/api.md`. Use `db(read_only=True)`. Parse
`json` columns before returning — the frontend expects objects, not strings.

## 5. Traps

- `is_ontime_15` on `mis.trips` is precomputed as
  `actual_end_epoch − planned_end_epoch ≤ 15 min`. Use it. Never touch `delay_reason`
  or `delay_minutes` for punctuality.
- Minimum sample for a signal worth raising is **n ≥ 40**. Raise below that anyway but
  set `severity = 'low'`; B suppresses it with reason `small_sample`.
- `shift_type` contains `'Non Shift'` (12,446 trips) and `'Adhoc'` (2,353). `shift_hour`
  is already null for those. Never parse `shift_type` yourself.
- `mis.bills.trip_id` is null on 160 rows where the raw value is the string
  `'OverHead'` (₹4.46M). Handle it; do not crash on it.
- Billing cycles are semi-monthly plus a monthly roll-up. Fire `billing_anomaly` on
  cycle close, not daily.
- Baselines need warm-up. Emit nothing where `baseline_n < 3`; the replay starts
  1 May precisely so July has a mature baseline.

## 6. Done when

- [ ] `make replay --only sense` completes over all 92 days with no traceback
- [ ] Running it twice produces identical row counts (idempotent ids)
- [ ] `field_trust` has ≥ 8 rows; `delay_minutes` and `marshal_rating` are `quarantined`
- [ ] A detector asking for a quarantined column raises, loudly and logged
- [ ] Sunday produces **zero** `punctuality_drop` signals — the weekday-aware proof
- [ ] July yields roughly 84 raw signals across all detectors
- [ ] `metric_integrity` fires on Santa Clara for 19–20 July
- [ ] 21 July produces signals for Cedar Ridge **and** for Sneha and Meera Pavlov,
      all three carrying `parent_id = 'vanta-Aus'`
- [ ] `counterfactual` has `schedule_pad_min` rows for every business unit, and
      vanta-Aus at param `10` shows `projected_value ≈ 90.4`
- [ ] All four endpoints return the shapes in `contracts/api.md`
- [ ] `make test` passes
- [ ] Everything committed and pushed

## 7. Tests

`tests/test_sense.py`, pytest, no fixtures framework. Cover:

1. `make_id` is stable across calls
2. A Sunday with 380 trips at 96% on-time raises no signal
3. `metric_integrity` fires when planned duration jumps and actual duration does not
4. `schedule_pad_min` at 10 minutes reproduces 90.4% for vanta-Aus in July
5. Requesting a quarantined column raises
6. `run_day` twice for one date leaves row counts unchanged
