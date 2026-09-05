# Demo cases — what actually fires when we stream July

Answers the question "will anything appear on screen?" by simulating the detectors
against the real data. Every case below is a real event on a real date in the replay
window, with the query that produces it.

**Replay window for the demo: July 1–31, 2026.** 31 days, ~9,700 trips/weekday.
Memory warms up on May 1 – Jun 30 (61 days) and is *not* scored.

---

## The headline discovery: three metrics that improved without anything improving

This is the novelty. Not "an agent that finds bad vendors" — every team will do that.
**An agent that audits whether an improvement is real.** Three independent, provable
instances exist in this dataset, and one of them lands inside the demo window.

### 1. Santa Clara, July 19 — punctuality jumps 16.5 points, journeys get *longer*

| | Jul 1–18 | Jul 19–31 | Change |
|---|---|---|---|
| On-time arrival | 35.8% | **52.3%** | **+16.5 pts** |
| Planned trip duration | 40.6 min | **58.1 min** | **+43.1%** |
| **Actual trip duration** | **76.4 min** | **77.0 min** | **+0.6 min** |
| Planned km | 20.31 | 20.20 | − |
| Seat utilisation | 0.740 | 0.751 | − |
| Distinct cabs | 334 | 320 | −14 |

Same routes, same distance, slightly *fewer* cabs, and a journey that is six seconds
per minute longer. The entire 16.5-point gain came from moving the finish line 17.5
minutes later.

**This fires live on July 20 in the demo.** Every dashboard in the room celebrates.
The agent says: *"This is not an improvement. Planned duration rose 43% on unchanged
routes; actual journey time is unchanged at 77 minutes. Your employees' commute did
not improve by one second."*

### 2. pinnacle-Slc alert response — a 2.8× "improvement" from deleting an alert type

| Month | Alerts | Mean ack | Like-for-like ack¹ |
|---|---|---|---|
| May | 10,350 | 1,214.7 min | 562.2 min |
| Jun | 3,471 | 474.6 min | 474.6 min |
| Jul | 3,355 | 438.5 min | 438.5 min |

¹ excluding `EMPLOYEE_SIGN_OFF_TIME_VIOLATION`

`EMPLOYEE_SIGN_OFF_TIME_VIOLATION` — 7,664 alerts in May at 1,444.0 min — **stops being
generated entirely from June onward.** The headline improvement is 2.8×; the
like-for-like improvement is 22%.

And the proof that nothing was fixed: `DEVICE_NOT_REACHABLE` mean acknowledgement runs
**1,444.4 → 1,444.8 → 1,444.9 minutes** across the three months. Unchanged to one
decimal place, at just under 24 hours, on ~1,000 alerts a month.

### 3. The whole fleet — `delay_reason` says 90.2% on time, the clocks say 64.9%

Covered in [`01-data-analysis.md`](01-data-analysis.md). Same failure mode, at the
level rather than the trend.

**Why this is the pitch:** any agent can detect a metric going down. An agent that
detects a metric going *up for the wrong reason* is auditing its own organisation.
Three instances, all provable, all invisible to a dashboard.

---

## The recurring incident — vanta-Aus / Cedar Ridge through July

This is the case-memory arc. It is real, it escalates, and it spans the whole replay.

Daily on-time at Cedar Ridge (vanta-Aus), baseline ~88%:

| Date | Day | Trips | On-time | Traffic | Driver | Detector |
|---|---|---|---|---|---|---|
| Jul 7 | Tue | 1,007 | 78.6% | 3.4% | 1.0% | — |
| **Jul 8** | Wed | 990 | **72.9%** | 3.5% | 2.2% | **fires, z = −2.08** |
| Jul 15 | Wed | 1,003 | 77.8% | 2.6% | 0.9% | — |
| **Jul 21** | Tue | 978 | **59.3%** | **10.1%** | **4.7%** | **fires, z = −4.18** |
| Jul 22 | Wed | 980 | 73.1% | 4.7% | 1.5% | — |
| **Jul 28** | Tue | 942 | **67.0%** | 4.4% | 2.1% | **fires, z = −2.16** |
| **Jul 29** | Wed | 924 | **60.5%** | 7.4% | 3.6% | **fires, z = −2.59** |
| Jul 30 | Thu | 981 | 69.6% | 7.3% | 3.7% | — |

Four detector firings, escalating, on the same entity — and the pattern is
**Tuesday/Wednesday**:

| Day | Trips | On-time |
|---|---|---|
| Tuesday | 3,934 | **73.4%** |
| Wednesday | 4,928 | **74.2%** |
| Thursday | 4,978 | 81.8% |
| Friday | 4,586 | 86.3% |
| Monday | 3,732 | 87.1% |

So the agent has something genuinely predictive to say on July 8, and three later
chances to be proved right.

### July 21 is not a vendor failure — and the agent must not treat it as one

A naive per-vendor detector fires **two separate alerts** on July 21:

| Vendor | Jul 21 on-time | Baseline | z |
|---|---|---|---|
| Sneha Mikhailov Travel | 63.2% | 89.9% | −4.44 |
| Meera Pavlov Travel | 56.9% | 89.7% | −4.01 |

But all five vendors at the site were hit:

| Vendor | Trips | On-time |
|---|---|---|
| Sneha Mikhailov Travel | 265 | 63.0% |
| Priya Mikhailov Travel | 225 | 56.0% |
| Meera Pavlov Travel | 194 | 56.7% |
| Anjali Mikhailov Travel | 169 | 60.9% |
| Sanjay Mikhailov Travel | 118 | 56.8% |

**Correct behaviour: suppress the two vendor alerts, raise one site incident, and state
explicitly that no vendor penalty is warranted.** Alert correlation and false-positive
suppression — worth more to a transport manager than any chart.

### Hypothesis elimination, with a clean control group

vanta-Aus is the only unit declining monotonically (89.5% → 84.7% → 81.5%) while
simultaneously shifting to EVs (27.0% → 35.4%) and larger cabs (avg capacity 3.75 → 3.99).
The obvious hypothesis is that electrification is costing punctuality. It is wrong:

| Fuel | May | Jun | Jul |
|---|---|---|---|
| Diesel | 89.5% | 84.8% | 81.7% |
| Electric | 89.3% | 84.3% | 81.1% |

They decline in lockstep. Control group at orbit-Slc: Electric 72.5% vs Diesel 71.7% —
no penalty. **Hypothesis refuted.**

What actually moved: actual minutes per km at vanta-Aus went 3.194 → 3.530 (**+10.5%**)
while planned minutes per km went 3.003 → 3.106 (**+3.4%**). Journeys genuinely got
slower and the schedule did not follow. That is the diagnosis, and it is the *opposite*
of the Santa Clara case — which is why the agent needs both detectors.

---

## Daily case inventory for the July replay

Every demo day has content. Counts are actual.

| Signal | Frequency in July | Example |
|---|---|---|
| Vendor punctuality anomaly (z < −2, n ≥ 30) | 8 firings | Jul 21 Sneha, z = −4.44 |
| Site punctuality anomaly (z < −2, n ≥ 40) | 5 firings | Jul 21 Cedar Ridge, z = −4.18 |
| Metric-integrity breach | 1 in window (+2 historical) | Jul 19 Santa Clara |
| Sev-1 alert cluster (≥ 12/day) | 5 days | Jul 15 (23), Jul 16 (22), Jul 8 (21) |
| Escort-policy breach | **every day**, 69–98 trips/day | Jul 13: 228 alerts, 96 trips |
| Alert-volume spike (z > 2.5) | 1 | Jul 15 vanta-Aus, 162 vs 54.7 |
| No-show spike (z > 2.5) | 2 | Jul 29 Clearwater, 3.3% vs 1.1% |
| Billing-cycle anomaly | Jul 31 (cycle close) | ₹270M, 42.1% zero-km |

**Sev-1 clusters are entirely catalyst-Sac**, driven by `OVER_SPEEDING` (6–15/day) plus
`PANIC_DEVICE`. That matters because catalyst-Sac also has the **worst acknowledgement
time in the fleet at 993 min** — and unlike pinnacle-Slc it has not moved in three
months (965 → 915 → 993). The unit generating the most severe safety events is the
slowest to answer them. That is the memo's lead paragraph.

---

## Can the agent propose *solutions*, or only suggestions?

**Honest finding: the dataset contains almost no interventions.** Vendor mix is frozen
(Vikram holds 48.9% of Santa Clara in both May and July); no vendor–office pair starts
or stops mid-window; fleet size is flat. Nobody acted on anything, so the agent cannot
learn "action X worked" from history.

There are exactly two things resembling an intervention, and **both are the fake
improvements above** — which is itself the finding.

So recommendations must be grounded a different way. Three mechanisms, in descending
order of rigour:

### A. Exact recomputation — a real counterfactual, not an estimate

Punctuality is recomputed from timestamps, so schedule changes can be evaluated exactly
by re-running the comparison against a shifted target:

| vanta-Aus, July | On-time |
|---|---|
| As scheduled today | 81.5% |
| Planned end + 5 min | 86.7% |
| Planned end + 10 min | 90.4% |

Fully determined by the data. And the agent must say the honest second half:
*"this moves the metric, not the commute — it is the same trick Santa Clara used on
July 19. Recommend only alongside a real journey-time fix."*

### B. Peer-substitution arithmetic — quantified, with stated assumptions

Santa Clara in July: Vikram 6,401 trips at 43.2%, Rohan 3,196 at 41.3%, Divya 3,218 at
42.1% — against Priya at 83.3% on 60 trips. The agent can compute the effect of
reallocating volume at observed rates, **and must flag n = 60 as too small to be a
reliable estimate.** Knowing when the counterfactual is weak is the point.

### C. Internal benchmark transfer — "your own org already solved this"

orbit-Slc and vanta-Aus acknowledge alerts in 3–5 minutes. catalyst-Sac takes 993.
Same platform, same alert types. The recommendation is not "improve response times";
it is *"replicate orbit-Slc's process — they answer the same alerts 200× faster."*

**Verdict:** the agent proposes solutions with computed effect sizes and explicit
assumptions, labelled as projections. It never claims a verified outcome, because no
verified outcome exists in this data. Saying so out loud is stronger than pretending.

---

## Consequences for the design

Four changes fall out of the above.

1. **Day-of-week baselines are mandatory.** Fleet on-time is 96% on Sundays and 60% on
   Tuesdays. A naive daily detector fires a false positive *every Sunday* — visible in
   the data as z ≈ +2.1 on Jun 7, Jun 14, Jun 28, Jul 5, Jul 12, Jul 19. Baselines must
   be trailing same-weekday.

2. **Playbooks are earned by verified predictions, not verified actions.** There are no
   actions to learn from. The agent attaches a falsifiable prediction with a date to each
   case; the verifier checks it against later replay days. On Jul 8 it predicts Cedar
   Ridge recurrence; Jul 21, 28 and 29 confirm it. Diagnostic confidence rises on
   evidence — a genuine learning loop the data actually supports.

3. **Metric-integrity is a first-class sensor, not a footnote.** It fires when a metric
   improves while its underlying quantity does not: schedule padding, denominator
   changes, alert-type deletion. Three provable instances.

4. **Alert correlation before escalation.** July 21 proves a per-entity detector produces
   two vendor alerts for one site event. Correlate by time and parent entity, suppress
   children, raise one incident.

---

## Reproducing every case here

```bash
uv run analytics/mis.py findings          # the standing claims
uv run analytics/mis.py shell             # then paste any query from this doc
```

The detector simulations use a trailing-28-day window:

```sql
window w as (partition by <entity> order by trip_date
             range between interval 28 days preceding and interval 1 day preceding)
```

Add `and dayname(trip_date) = dayname(<row>)` to the partition for the weekday-aware
version described in consequence 1.

---

## The suppression ledger — 84 raw signals, 10 incidents

Simulated over July with a naive detector (z-threshold only, no weekday adjustment,
no minimum sample, every entity level independent):

| Entity level | Raw signals | Negative | Positive |
|---|---|---|---|
| Office | 39 | 9 | 30 |
| Vendor | 37 | 14 | 23 |
| Business unit | 8 | 4 | 4 |
| **Total** | **84** | 27 | 57 |

Most of those are noise, and each kind of noise has a *nameable* reason. This is the
part worth showing: not that the agent detects, but that it **explains away** and shows
its working.

### Reason 1 — Composition, not performance

| Day | Signals | Avg trips/day | Avg z |
|---|---|---|---|
| **Sunday** | **38** | **6** | **+2.11** |
| Saturday | 18 | 25 | +0.84 |
| Tuesday | 12 | 402 | −1.91 |
| Wednesday | 7 | 648 | −1.60 |
| Monday | 4 | 144 | +2.48 |

Fleet on-time is 96% on Sundays and 60% on Tuesdays. **56 of 84 signals are weekend
artifacts** — a naive detector reports a punctuality triumph every Sunday on six trips.
Suppression reason: *"Sunday volume is 375 trips against a 9,700 weekday norm; the trip
mix differs, the performance does not."*

### Reason 2 — Sample too small to be signal

58 of 84 signals have n < 40, at a mean |z| of 2.56 — statistically loud, operationally
meaningless. Suppression reason: *"n = 23; below the threshold at which this z is
distinguishable from variance."*

### Reason 3 — Child of a parent event

July 21 raises two independent vendor alerts (Sneha z = −4.44, Meera z = −4.01). Both
belong to one site incident: all five vendors at Cedar Ridge were hit that day.
Suppression reason: *"folded into incident #4 — vanta-Aus site-wide, 5 of 5 vendors
affected. No vendor penalty warranted."*

Note the design requirement this exposes: correlation needs a **vendor → business unit
→ office** hierarchy to fold children into parents. Without it the two vendor alerts
survive as separate incidents, which is exactly the failure mode being fixed.

### Reason 4 — Known pattern (memory suppresses)

By July 28, Cedar Ridge Tuesday/Wednesday degradation has fired three times. The fourth
firing should **not** open a new incident. It updates the open case and escalates its
classification from *incident* to *structural*.

This is a **reclassification, not a suppression** — 60.5% on July 29 is not fine, it is
chronic. Two distinct outputs worth keeping distinct:

- **Suppressed** — no action needed, reason shown on click
- **Reclassified** — still matters, but it is not news; the recommendation changes from
  "investigate" to "fix the Tuesday capacity plan"

### Reason 5 — The target moved, not the performance

The inverse case, and the best one: a **positive** anomaly rejected. Santa Clara's
July 19 jump of +16.5 points is suppressed with *"planned duration rose 43%; actual
journey time unchanged at 77 minutes. Improvement rejected."*

An agent that refuses to take credit for a fake improvement is more convincing than one
that finds problems.

### Net result

**84 raw signals → 10 surviving incidents.** Every one of the 74 suppressions carries a
stated reason, visible on one click.

| Date | Level | Entity | Trips | On-time | Baseline | z |
|---|---|---|---|---|---|---|
| Jul 8 | BU | vanta-Aus | 990 | 72.9% | 88.4% | −2.07 |
| Jul 8 | Vendor | Meera Pavlov Travel | 222 | 70.7% | 89.2% | −2.34 |
| Jul 21 | BU | vanta-Aus | 978 | 59.3% | 89.0% | −4.17 |
| Jul 21 | Vendor | Sneha Mikhailov Travel | 266 | 63.2% | 89.9% | −4.44 |
| Jul 21 | Vendor | Meera Pavlov Travel | 195 | 56.9% | 89.7% | −4.01 |
| Jul 28 | BU | vanta-Aus | 942 | 67.0% | 87.1% | −2.18 |
| Jul 28 | Vendor | Meera Pavlov Travel | 180 | 62.8% | 87.7% | −2.37 |
| Jul 29 | BU | vanta-Aus | 924 | 60.5% | 86.4% | −2.59 |
| Jul 29 | Vendor | Meera Pavlov Travel | 192 | 62.0% | 86.7% | −2.15 |
| Jul 30 | Vendor | Sneha Mikhailov Travel | 262 | 69.1% | 88.1% | −2.20 |

The vendor rows here are the ones reason 3 folds into their parent once the hierarchy
is wired — taking the true count to roughly **five incidents, all one story**: the
vanta-Aus Cedar Ridge Tuesday/Wednesday arc.

That is the demo. Eighty-four alarms, one actual problem, and the agent shows you why
it discarded the other eighty-three.
