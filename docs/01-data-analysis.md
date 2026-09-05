# Data analysis — what is actually in the MoveInSync dataset

Every number here was computed from the raw CSVs and is reproducible:

```bash
uv run analytics/mis.py build
uv run analytics/mis.py findings          # all of it
uv run analytics/mis.py findings F-04     # one claim
```

The finding ID in each heading is the query name in `analytics/findings.sql`.

**Currency caveat.** The dataset never states a currency. `₹` is assumed from the
magnitude of `trip_cost` (median ₹1,200/trip, ~₹83/km). Carry that caveat on any
headline figure you put in front of a judge.

---

## Scope `F-00`

| | |
|---|---|
| Trips | 615,546 (May 188,992 · Jun 210,669 · Jul 215,885) |
| Rider legs | 1,637,906 |
| Billing lines | 620,942 |
| Feedback rows | 512,873 |
| Alerts | 51,699 |
| Window | 2026-05-01 → 2026-07-31 (92 days, ~6,690 trips/day) |
| Business units | 5 · Offices 19 · Vendors 23 · Cabs ~3,500 |

Join coverage after normalising `trip_id`: bills → trips **99.1%**, alerts → trips
**99.1%**, rider legs → trips **100%**, feedback → trips **99.97%**. The joins are
sound; the dataset is genuinely relational, not five unrelated extracts.

---

## The headline: the platform's own punctuality field is wrong `F-HOOK`

| Measure | Value |
|---|---|
| Trips with `delay_reason = 'NODELAY'` | **90.2%** |
| Trips arriving within 15 min of plan (from epochs) | **64.9%** |
| `corr(delay_minutes, departure slip)` | **0.04** |
| `corr(delay_minutes, arrival slip)` | 0.84 |
| Rows where `delay_minutes = 0` | 90.2% |

`delay_minutes` is populated only when `delay_reason ≠ NODELAY`, so it is a manually
attributed field, not a measurement. A dashboard bound to it reports punctuality
**25 percentage points** better than reality.

Recomputing from `actual_end_epoch − planned_end_epoch` gives the true distribution:

| Statistic | Arrival delay (min) |
|---|---|
| Mean | 11.03 |
| p50 | 7.47 |
| p90 | 39.03 |
| p99 | 96.92 |
| % > 5 min late | 55.4% |
| % > 15 min late | 35.1% |

**Definition used throughout this repo:** on-time = `actual_end_epoch − planned_end_epoch ≤ 15 min`.

---

## The incident: punctuality collapses in June, then recovers `F-TREND` `F-CAUSE`

Weekly on-time arrival. The week of Apr 27 is excluded — the data starts Friday
May 1, so it holds 3,168 trips against a ~47,000 weekly norm; including it shows a
fake 82% starting point.

| Week | Trips | On-time |
|---|---|---|
| May 4 | 46,807 | 70.5% |
| May 11 | 48,609 | 69.1% |
| May 18 | 49,267 | 62.6% |
| May 25 | 41,141 | 64.3% |
| **Jun 1** | 47,322 | **56.0%** ← trough |
| Jun 8 | 48,999 | 58.5% |
| Jun 15 | 48,172 | 62.4% |
| Jun 22 | 47,719 | 65.4% |
| Jun 29 | 47,237 | 65.1% |
| Jul 6 | 47,394 | 65.0% |
| Jul 13 | 47,161 | 67.7% |
| Jul 20 | 46,992 | 67.1% |
| Jul 27 | 45,558 | 69.8% |

### Root cause, one join away

`pinnacle-Slc` is the largest unit (251,774 trips) and drives the shape:

| Month | Trips | Unique cabs | Trips per cab | On-time |
|---|---|---|---|---|
| May | 75,165 | 1,515 | 49.6 | 68.0% |
| Jun | 88,035 | 1,517 | 58.0 | **60.2%** |
| Jul | 88,574 | 1,497 | 59.2 | 69.8% |

Demand **+17.1%**, fleet **+0.1%**, load per vehicle **+17%**, punctuality **−7.8 pts**.
A demand shock absorbed by an unchanged fleet.

July is the control: volume flattens (+0.6%) and punctuality recovers to 69.8% on a
fleet that is *smaller* (1,497 cabs) — so the recovery is not capacity, it is the
system re-planning around the new load. That distinction is exactly the kind of thing
an agent should be able to state and a dashboard cannot.

By business unit:

| Business unit | May | Jun | Jul | Trips |
|---|---|---|---|---|
| pinnacle-Slc | 68.0% | 60.2% | 69.8% | 251,774 |
| vanta-Sea | 64.5% | 58.7% | 63.6% | 180,064 |
| vanta-Aus | 89.5% | 84.7% | 81.5% | 70,199 |
| catalyst-Sac | 37.3% | 38.8% | 47.8% | 65,214 |
| orbit-Slc | 73.8% | 69.7% | 72.3% | 48,295 |

`vanta-Aus` is the only unit declining monotonically — a second, quieter incident
hiding under the fleet-wide recovery.

---

## Safety and governance

### A 282× acknowledgement gap between business units `F-01`

| Business unit | Alerts | Mean ack | p90 ack |
|---|---|---|---|
| orbit-Slc | 1,202 | **3.4 min** | 7 min |
| vanta-Aus | 5,142 | 4.6 min | 9 min |
| vanta-Sea | 20,105 | 13.9 min | 33 min |
| pinnacle-Slc | 17,176 | **912.8 min** | 1,447 min |
| catalyst-Sac | 8,074 | **959.2 min** | 1,447 min |

Same platform, same alert types, same severities. Two units answer safety alerts in
minutes; two take sixteen hours. The p90 of 1,447 min ≈ 24h suggests a daily batch
review rather than a response process.

### 94% of "woman travelling alone" alerts fire with no escort `F-02`

| Escort present | Trips with a `WOMAN_TRAVELLING_ALONE` alert |
|---|---|
| `false` | **5,126** |
| `true` | 330 |

10,669 such alerts across 5,430 trips. The alert exists to catch this condition, and
the control that should answer it is absent 94% of the time it fires.

Fleet-wide escort coverage is thin: 101,662 of 615,546 trips (16.5%) ran with an
escort, and it is heavily skewed to LOGOUT (85,118) over LOGIN (16,544).

### Emergencies answered, compliance ignored `F-03`

| Event type | Alerts | Mean ack |
|---|---|---|
| `PANIC_DEVICE` | 786 | **0.7 min** |
| `OVER_SPEEDING` | 1,289 | 2.5 min |
| `VEHICLE_STOPPAGE` | 8,730 | 6.9 min |
| `FIRST_MALE_NO_SHOW` | 130 | 9.9 min |
| `PANIC_MOBILE` | 202 | 3.2 min |
| `WOMAN_TRAVELLING_ALONE` | 10,669 | 12.1 min |
| `PANIC_FIXED_DEVICE` | 1,446 | 12.1 min |
| `DEVICE_NOT_REACHABLE` | 9,914 | 475 min |
| `EMPLOYEE_GEOFENCE_VIOLATION` | 10,796 | 718 min |
| `EMPLOYEE_SIGN_OFF_TIME_VIOLATION` | 7,736 | **1,431 min** |

The split is clean: anything that looks like an emergency is answered in minutes;
anything preventive rots for hours or a day. That is a staffing and policy finding,
not a metric.

Alert state is almost entirely `CLOSED` (51,646 of 51,699) — 52 `NEW`, 1 `OPEN`. So
closure rate is not a useful signal here; **time to acknowledge** is.

---

## Cost and billing integrity

Total billed across the quarter: **₹833,976,771**.

| Month | Lines | Spend | Billed km | Cost/km |
|---|---|---|---|---|
| May | 191,266 | ₹254.6M | 1,705,683 | ₹149.27 |
| Jun | 212,486 | ₹284.8M | 1,990,770 | ₹143.07 |
| Jul | 217,190 | ₹294.6M | 2,106,658 | ₹139.82 |

### 45% of spend has no distance evidence `F-04`

| | |
|---|---|
| Lines with `total_trip_km = 0` | **39.97%** |
| Spend on those lines | **₹378,760,438** (45.4% of total) |

Some vendors bill this way on essentially every line:

| Vendor | Lines | % zero-km | Zero-km spend |
|---|---|---|---|
| Sneha Mikhailov Travel | 19,530 | **100.0%** | ₹24.4M |
| Meera Pavlov Travel | 15,712 | **100.0%** | ₹20.3M |
| Aarav Petrov Travel | 15,329 | 98.0% | ₹22.1M |
| Isha Mikhailov Travel | 30,959 | 97.2% | ₹45.1M |
| Priya Mikhailov Travel | 57,211 | 92.9% | ₹78.1M |
| Anjali Mikhailov Travel | 50,886 | 70.4% | ₹57.4M |
| Sanjay Mikhailov Travel | 74,851 | 63.4% | ₹76.4M |

This may be legitimate fixed-slab billing. The point is that **nothing in the data
distinguishes a fixed-slab contract from a telemetry failure**, and no one is
currently asking. That ambiguity is itself the finding.

### 6,999 trips billed twice `F-05`

13,998 duplicate lines, **₹18,753,571**. Provable, recoverable money.

### Contract arbitrage `F-06`

Cost per km on lines with real distance:

| Contract | Lines | Cost/km |
|---|---|---|
| `6S-EV-HTK` | 4,068 | ₹117.82 |
| `6Seater` | 18,359 | ₹105.45 |
| `5S_Jan2024_CNG_AC` | 3,791 | ₹98.92 |
| `DV_Package` | 78,177 | ₹95.65 |
| **`4Seater`** | **151,586** | **₹82.90** |
| `3S_Jan2024_CNG_AC` | 55,140 | ₹73.65 |
| `NPT_4_SEATER` | 10,024 | ₹61.25 |
| `8SEATER_BTT_2025` | 4,120 | ₹60.84 |
| `4SEATER_BTT_2025` | 7,614 | ₹55.20 |
| **`LVT_4_SEATER_EV`** | 3,549 | **₹49.60** |
| **`4Seater-LVT-July`** | 10,695 | **₹49.42** |

Four-seater service runs from ₹49.42 to ₹82.90 per km depending only on the contract
code. The dominant contract (151,586 lines) is the expensive one.

### Solo riders `F-07`

| | |
|---|---|
| Fleet mean seat utilisation | **0.611** |
| Trips carrying exactly one rider | **31.6%** |
| Trips with 1 rider in a 4+ seat cab | 25,790 |
| Spend on those | **₹27,594,087** |

| Business unit | Seat util | % solo | Solo spend (4+ seat) |
|---|---|---|---|
| pinnacle-Slc | 0.576 | 44.9% | ₹2.9M |
| vanta-Sea | 0.596 | 26.8% | ₹4.0M |
| orbit-Slc | 0.650 | 18.7% | ₹1.8M |
| vanta-Aus | 0.657 | 21.4% | ₹7.9M |
| catalyst-Sac | 0.705 | 14.0% | ₹11.0M |

Note the inversion: `catalyst-Sac` has the *best* utilisation but the *highest* solo
spend, because its solo trips are long and expensive. Ranking on either metric alone
gives the wrong answer — which is the argument for contextual metrics in one line.

### Cost per trip `F-07`

| Business unit | Spend | Cost/trip |
|---|---|---|
| catalyst-Sac | ₹102.3M | ₹1,567 |
| vanta-Sea | ₹278.7M | ₹1,528 |
| orbit-Slc | ₹66.1M | ₹1,338 |
| vanta-Aus | ₹92.6M | ₹1,305 |
| pinnacle-Slc | ₹294.2M | ₹1,164 |

---

## Vendor and site performance

### The invisible vendor `F-09`

| Vendor | Trips | % of fleet | On-time | Mean delay |
|---|---|---|---|---|
| **Pooja Sokolov Travel** | **556** | **0.090%** | **21.9%** | **246.7 min** |
| Vikram Mikhailov Travel | 25,019 | 4.06% | 40.2% | 34.5 min |
| Meera Lebedev Travel | 1,200 | 0.19% | 50.4% | 19.7 min |
| Divya Mikhailov Travel | 28,331 | 4.60% | 54.3% | 19.3 min |
| Arjun Mikhailov Travel | 18,783 | 3.05% | 60.2% | 14.0 min |

Pooja Sokolov is worst-in-fleet by a factor of four on mean delay and has been every
week for three months (weekly on-time: 20.0, 23.7, 7.7, 24.0, 7.1, 3.1, 42.9, 30.4,
13.2, 22.7, 17.1, 31.8, 15.5%). At 0.09% of volume it never enters a top-N table,
never moves a fleet average, and never trips a threshold alert.

**This is the demo.** "Surface what matters, not what is large" has a concrete
instance in this dataset, and it is invisible to every conventional dashboard.

### Trend flips the recommendation `F-10`

| Vendor | May | Jun | Jul | Verdict |
|---|---|---|---|---|
| Vikram Mikhailov Travel | 34.4% | 37.6% | 47.0% | bad, **improving** → hold and monitor |
| Pooja Sokolov Travel | ~19% | ~19% | ~21% | bad, **flat** → terminate |

Same detector, opposite action, purely because of trend. This is the cheapest possible
demonstration of why context is mandatory.

### Santa Clara is a sourcing problem, not a traffic problem `F-11`

| Vendor at Santa Clara Office | Trips | On-time |
|---|---|---|
| Vikram Mikhailov Travel | 19,582 | 37.1% |
| Divya Mikhailov Travel | 9,817 | 35.9% |
| Rohan Mikhailov Travel | 9,682 | 36.5% |
| Arjun Mikhailov Travel | 112 | 48.2% |
| Amit Mikhailov Travel | 230 | 40.4% |
| **Priya Mikhailov Travel** | **202** | **71.8%** |

The three main vendors cluster at 36–37%, which looks structural — until Priya runs
the same site at 71.8%. The routes are servable. The vendors are the problem.

**This is the reasoning-layer showcase:** a hypothesis ("the site is congested") that
looks obviously right on the aggregate and is refuted by a small counter-example.

### Site scorecard `F-11b`

| Office | BU | Trips | On-time | Seat util | EV % |
|---|---|---|---|---|---|
| Santa Clara Office | catalyst-Sac | 39,591 | **36.7%** | 0.76 | 0.0% |
| Redwood City Center | catalyst-Sac | 5,587 | 43.8% | 0.53 | 0.0% |
| Fairview Commons | catalyst-Sac | 8,213 | 47.3% | 0.64 | 0.0% |
| Crestwood Campus | catalyst-Sac | 10,213 | 55.9% | 0.66 | 0.0% |
| Clearwater Campus | pinnacle-Slc | 114,174 | 62.0% | 0.62 | 0.0% |
| Denver Office | vanta-Sea | 179,655 | 62.2% | 0.60 | 17.8% |
| Willow Bend Campus | pinnacle-Slc | 69,868 | 68.7% | 0.54 | 0.0% |
| Oakmont Office | pinnacle-Slc | 64,667 | 70.2% | 0.53 | 0.0% |
| Cedar Ridge Office | orbit-Slc | 10,264 | 71.0% | 0.63 | 11.4% |
| Lakeside Commons | orbit-Slc | 21,063 | 71.8% | 0.61 | 16.5% |
| Eastgate Office | orbit-Slc | 16,968 | 72.4% | 0.71 | 19.4% |
| **Cedar Ridge Office** | **vanta-Aus** | 69,801 | **85.1%** | 0.66 | 31.6% |

Note `Cedar Ridge Office` appears under two business units with a 14-point punctuality
gap — the same site name, different operations. Any peer comparison must key on
`(business_unit, office)`, not `office`.

---

## Sustainability `F-12`

| Business unit | Total km | EV share of km |
|---|---|---|
| vanta-Aus | 1,129,421 | **32.3%** |
| vanta-Sea | 2,705,274 | 18.0% |
| orbit-Slc | 1,105,452 | 15.3% |
| catalyst-Sac | 1,338,643 | **0.0%** |
| pinnacle-Slc | 3,259,446 | **0.0%** |

Two units run 4.60M of the fleet's 8.32M km (55%) with zero electrification. Fleet-wide
EV share of km moves 10.47% → 10.52% → 11.10% across the three months — essentially flat.

Fuel mix is stable at roughly 52% diesel / 37% petrol / 11% electric. With
`fuel_type` and `traveled_km` you can attach a defensible CO₂e figure using standard
emission factors, and a peer target from `vanta-Aus` in the same sentence.

---

## Employee impact

### No-shows `F-13`

118,032 no-shows across 1,637,906 legs (7.2%).

| Office | Legs | No-show |
|---|---|---|
| Denver Office | 588,167 | **13.19%** |
| Cedar Ridge Office | 194,569 | 11.18% |
| Lakeside Commons | 65,900 | 9.91% |
| Eastgate Office | 50,042 | 5.53% |
| Clearwater Campus | 270,279 | 1.23% |
| Willow Bend Campus | 127,647 | 1.08% |
| Santa Clara Office | 129,557 | 1.07% |
| Oakmont Office | 112,044 | **0.80%** |

By gender: **FEMALE 8.66%** vs **MALE 6.09%**.

> **Read this one carefully.** `signintype` is null on exactly 190,009 legs, and those
> legs are **62.1%** no-show against ~0% everywhere else. Null `signintype` means the
> leg was never picked up. So the 16× office spread is likely a *recording* difference,
> not a behaviour difference. An agent that says so is more credible than one that
> ranks offices on it. This is the best test case in the dataset for whether your
> reasoning layer knows when to withhold a conclusion.

### Rider-level lateness `F-14`

| | |
|---|---|
| Legs picked up >10 min late | **11.28%** |
| p90 drop delay | **27.3 min** |

Rolled up to `office × shift_type`, this is the line manager's real question: how much
of my team's week is spent in a cab that is behind schedule, and is my shift worse
than the site average?

---

## What has no signal `F-DEAD`

**Do not build a CSAT panel.** Ratings are flat to three decimal places across every
week and every business unit:

| Week | Responses | Driver | Safety | Route | Cab |
|---|---|---|---|---|---|
| May 4 | 37,935 | 4.894 | 4.895 | 4.885 | 4.895 |
| Jun 1 | 34,630 | 4.879 | 4.878 | 4.863 | 4.877 |
| Jul 27 | 41,608 | 4.889 | 4.889 | 4.876 | 4.887 |

Full range across all 14 weeks: driver 4.879–4.894, safety 4.877–4.895, route
4.863–4.885, cab 4.874–4.895. Total spread: **0.015 of a point**.

`marshal_rating` is 0 on **92.4%** of rows — unrated, not a low score. Averaging it in
would produce a fake 0.37/5 marshal score.

The one flicker: low driver ratings (≤2) rise from ~0.46% to **0.68%** in the week of
June 1 — the same week punctuality bottoms out. It is a real correlation and far too
weak to act on. Reporting it *with* that caveat is more valuable than reporting it as
a finding.

---

## Messy data inventory `F-MESS`

Six issues are in the data dictionary. Three are not, and those are the ones that
break naive pipelines.

| Issue | Scale | In the dictionary? |
|---|---|---|
| `trip_id` in three formats | all files | yes |
| `severity` holds the string `'False'` | 15,037 rows (+16,348 null) | yes |
| Negative `planned_km`/`traveled_km` | 48 legs | yes |
| Comma-formatted numerics | `trip_cost`, `delay_minutes`, 4 epoch cols | yes |
| dtype drift across the 3 monthly files | `is_driver_nc`, `is_cab_nc`, `planned_km` | yes |
| `stwid = 0` placeholder | most alert rows | yes |
| **`trip_id = 'OverHead'` in `bill_data`** | **160 lines, ₹4,457,560** | **no** |
| **`shift_type` = `'Non Shift'` / `'Adhoc'`** | **14,799 trips, 23,646 legs** | **no** |
| **Null `signintype` encodes "never picked up"** | **190,009 legs, 62.1% no-show** | **no** |

The last one is the dangerous one: it is not a parse error, so no validation catches
it, and dropping those rows silently deletes the entire no-show signal.

---

## Summary of what the data can and cannot support

**Supports well**
- Punctuality at trip, vendor, office, business-unit and shift grain, recomputed from timestamps
- Cost, contract and billing-integrity analysis at line-item grain
- Safety alert response SLAs, segmented by type and unit
- Escort and compliance policy adherence
- Fuel mix and distance-based emissions with a peer benchmark
- No-show and rider-lateness analysis, with caveats
- Day-by-day chronological replay: 92 days, ~6,690 trips/day, every table timestamped

**Does not support**
- Anything based on rider satisfaction — no variance exists
- Team or department rollups — **there is no team, department or manager column.**
  `employee_id` was removed during anonymisation. Closest proxies: `office × shift_type`
  (484 real combinations) or `emp_role = 'projectmgr'` (117,196 legs)
- Route-level geography — no coordinates, only distances
- Driver identity — only cab registration, so "driver performance" is really "vehicle performance"
