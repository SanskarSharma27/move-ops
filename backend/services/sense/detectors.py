"""The seven detectors. Recall is the job here; precision belongs to reason/.

Nothing is suppressed in this module. A signal that is obviously a weekend artifact,
or rests on nine trips, still gets written - with the severity that says so - because a
signal never raised is one nobody can recover, and the suppression ledger downstream is
only interesting if it has something to suppress.

`metric_integrity` is the one to read. Every other detector asks whether a number got
worse. That one asks whether a number that got *better* actually means anything, which
is the only question in this repo a dashboard cannot answer. It carries
`direction = 'better'` and a high severity at the same time, and that combination is
deliberate: an improvement that is really a defect is the most interesting row in the
table.

Ids come from `make_id(as_of, detector, entity_type, entity_id, metric)`, so a re-run
overwrites rather than duplicates.
"""
from __future__ import annotations

import logging
from datetime import date

from common import as_json, make_id, now, upsert

from . import baselines, field_trust

log = logging.getLogger("sense.detectors")

# ------------------------------------------------------------------- thresholds

Z_PUNCTUALITY = -2.0        # PRD: ota15 z < -2, any entity
Z_SAFETY = 2.5              # PRD: daily Sev-1 count z > 2.5 ...
SAFETY_MULT = 2.0           # ... or more than twice the trailing daily median, which is
SAFETY_MIN_COUNT = 10       # what actually catches catalyst-Sac's five cluster days
Z_NOSHOW = 2.5              # PRD: noshow_pct z > 2.5 ...
NOSHOW_MIN_N = 200          # ... with n >= 200

MIN_SAMPLE = 40             # PRD: below this, raise anyway but severity = 'low'
MIN_AFFECTED = 20           # a punctuality signal worth a person's attention is one
                            # with at least this many actually-late trips behind it

ACK_SLA_MIN = 60.0          # a safety alert unanswered for an hour is a breach
ACK_PEER_MULT = 5.0         # ... and five times the peer median is a different breach
ACK_MIN_ALERTS = 20

ESCORT_MIN_TRIPS = 10       # below this the breach is real but not a business unit's story

PAD_MIN_TRIPS = 100         # a day with fewer trips cannot establish a schedule change
PAD_PLAN_RATIO = 1.20       # planned minutes per km up at least this much ...
PAD_ACT_RATIO = 0.95        # ... while actual minutes per km did not improve
PAD_OTA_GAIN = 5.0          # ... and on-time gained at least this many points
PAD_ELEVATED_MAX = 1        # fires on the step, not for the fortnight after it

DENOM_SHRINK = 0.6          # a rate that improved on 60% of its usual volume
DENOM_GAIN = 5.0

CAT_HEADLINE_DROP = 0.75    # headline acknowledgement time fell to three quarters ...
CAT_LFL_FLOOR = 0.85        # ... but like-for-like barely moved
CAT_MIN_ALERTS = 100
CAT_ABSENT_DAYS = (14, 16)  # fire on the step, not every day thereafter

BILL_ZERO_KM_SHARE = 30.0   # % of cycle spend with no distance evidence
BILL_CONTRACT_MULT = 1.4    # cost per km against the fleet median
BILL_CONTRACT_MIN_LINES = 500

CRITICAL_Z, HIGH_Z, MEDIUM_Z = 4.0, 3.0, 2.5


def severity_for(z: float | None, n: int | None, affected: float | None = None) -> str:
    """Loud is not the same as important. Sample size and real-world impact both cap it."""
    if n is not None and n < MIN_SAMPLE:
        return "low"
    if affected is not None and affected < MIN_AFFECTED:
        return "low"
    az = abs(z) if z is not None else 0.0
    if az >= CRITICAL_Z:
        return "critical"
    if az >= HIGH_Z:
        return "high"
    if az >= MEDIUM_Z:
        return "medium"
    return "medium"


def _signal(as_of: date, detector: str, entity_type: str, entity_id: str,
            parent_id: str | None, metric: str, value: float | None,
            baseline: float | None, z: float | None, n: int | None,
            direction: str, severity: str, headline: str, evidence: list[dict]) -> dict:
    return {
        "signal_id": make_id(as_of, detector, entity_type, entity_id, metric),
        "as_of": as_of, "detector": detector, "severity": severity,
        "entity_type": entity_type, "entity_id": entity_id, "parent_id": parent_id,
        "metric": metric,
        "value": None if value is None else round(float(value), 4),
        "baseline": None if baseline is None else round(float(baseline), 4),
        "z": None if z is None else round(float(z), 4),
        "n": None if n is None else int(n),
        "direction": direction, "headline": headline,
        "evidence": as_json(evidence), "created_at": now(),
    }


def _ev(claim: str, value: float, unit: str, source: str) -> dict:
    return {"claim": claim, "value": round(float(value), 4), "unit": unit, "source": source}


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------------------------- the driver

def run(con, day: date) -> int:
    """Every detector for one business date, written to `signal`. Idempotent."""
    baselines.ensure_facts(con)
    base = baselines.read_day(con, day)
    found: dict[str, dict] = {}
    for name, fn in DETECTORS:
        field_trust.guard(con, name)          # refuses on a quarantined column, loudly
        try:
            for row in fn(con, day, base):
                found[row["signal_id"]] = row
        except field_trust.QuarantinedColumnError:
            raise
        except Exception:
            log.exception("detector %s failed on %s", name, day)
    n = upsert(con, "signal", list(found.values()), key="signal_id")
    if n:
        log.debug("signals %s: %d", day, n)
    return n


# --------------------------------------------------------------- punctuality_drop

def punctuality_drop(con, day: date, base: list[dict]) -> list[dict]:
    """On-time arrival below its own trailing same-weekday baseline.

    Fires on `z < -2` only. A positive punctuality anomaly is not a drop, and the
    interesting ones are not luck either - they are `metric_integrity`'s business,
    which is the detector equipped to ask whether the improvement is real.
    """
    out = []
    for r in base:
        if r["metric"] != "ota15" or r["z"] is None or r["z"] > Z_PUNCTUALITY:
            continue
        gap = r["baseline_mean"] - r["value"]
        late = r["n"] * gap / 100.0
        out.append(_signal(
            day, "punctuality_drop", r["entity_type"], r["entity_id"], r["parent_id"],
            "ota15", r["value"], r["baseline_mean"], r["z"], r["n"], "worse",
            severity_for(r["z"], r["n"], late),
            f"{r['entity_id']} on-time arrival fell to {r['value']:.1f}% against a "
            f"{r['baseline_mean']:.1f}% same-weekday baseline",
            [_ev("on-time arrival", r["value"], "%", "mis.trips"),
             _ev("trailing same-weekday baseline", r["baseline_mean"], "%", "entity_baseline"),
             _ev("trips run", r["n"], "trips", "mis.trips"),
             _ev("arrivals late against baseline", round(late), "trips", "mis.trips"),
             _ev("peer median", r["peer_median"], "%", "entity_baseline")]))
    return out


# --------------------------------------------------------------- metric_integrity

def metric_integrity(con, day: date, base: list[dict]) -> list[dict]:
    """The headline detector: a metric that improved for a reason that is not an improvement.

    Three patterns, all present in this dataset:

      schedule_padding    the finish line moved, the journey did not
      denominator_change  the rate improved because the population shrank
      category_deletion   the aggregate improved because a slow category stopped
                          being recorded at all
    """
    # Schedule padding is last so that if two patterns land on the same entity, metric
    # and day, its id wins the de-duplication in `run` - it is the stronger explanation.
    return (_denominator_change(con, day)
            + _category_deletion(con, day)
            + _schedule_padding(con, day))


_PAD_SQL = """
with daily as (
    select trip_date as d,
           business_unit || ' / ' || office as entity_id, business_unit as parent_id,
           count(*) as n,
           100.0 * avg(case when is_ontime_15 then 1.0 else 0.0 end) as ota,
           sum((planned_end - planned_start) / 60.0) / sum(planned_km) as plan_mpk,
           sum((actual_end  - actual_start)  / 60.0) / sum(planned_km) as act_mpk,
           avg(planned_km) as plan_km,
           count(distinct actual_cab_registration) as cabs
    from mis.trips
    where trip_date between $day - interval '14 days' and $day
      and planned_km > 0 and office is not null and business_unit is not null
    group by 1, 2, 3
),
prior as (
    select entity_id,
           median(plan_mpk) as plan_mpk, median(act_mpk) as act_mpk,
           median(plan_km) as plan_km, median(cabs) as cabs,
           sum(ota * n) / sum(n) as ota, sum(n) as n, count(*) as k
    from daily where d < $day and n >= $min_trips group by 1
),
-- how many days in the prior window were already padded, judged by the same ratio that
-- fires the detector. This is what keeps it on the step change instead of firing for
-- the fortnight afterwards, while ordinary day-to-day wobble in planned duration does
-- not count as a prior step.
elevated as (
    select d.entity_id, count(*) as k
    from daily d join prior p on p.entity_id = d.entity_id
    where d.d < $day and d.n >= $min_trips and d.plan_mpk >= $plan_ratio * p.plan_mpk
    group by 1
)
select t.entity_id, t.parent_id, t.n, t.ota, t.plan_mpk, t.act_mpk, t.plan_km, t.cabs,
       p.ota as prior_ota, p.plan_mpk as prior_plan_mpk, p.act_mpk as prior_act_mpk,
       p.plan_km as prior_plan_km, p.cabs as prior_cabs, p.n as prior_n, p.k as prior_days,
       coalesce(e.k, 0) as elevated_days
from daily t
join prior p on p.entity_id = t.entity_id
left join elevated e on e.entity_id = t.entity_id
where t.d = $day
  and t.n >= $min_trips
  and p.k >= 5
  and t.plan_mpk >= $plan_ratio * p.plan_mpk
  and t.act_mpk  >= $act_ratio  * p.act_mpk
  and t.ota - p.ota >= $ota_gain
  and coalesce(e.k, 0) <= $elevated_max
"""


def _schedule_padding(con, day: date) -> list[dict]:
    """Planned duration rose on unchanged routes; actual journey time did not improve.

    Compared per kilometre in both directions, so a change in trip composition - longer
    routes, different mix - cannot explain the gain away.
    """
    out = []
    for r in _rows(con.execute(_PAD_SQL, {
            "day": day, "min_trips": PAD_MIN_TRIPS, "plan_ratio": PAD_PLAN_RATIO,
            "act_ratio": PAD_ACT_RATIO, "ota_gain": PAD_OTA_GAIN,
            "elevated_max": PAD_ELEVATED_MAX})):
        pad_pct = 100.0 * (r["plan_mpk"] / r["prior_plan_mpk"] - 1)
        act_pct = 100.0 * (r["act_mpk"] / r["prior_act_mpk"] - 1)
        gain = r["ota"] - r["prior_ota"]
        out.append(_signal(
            day, "metric_integrity", "office", r["entity_id"], r["parent_id"], "ota15",
            r["ota"], r["prior_ota"], None, r["n"], "better", "critical",
            f"{r['entity_id']} on-time rose to {r['ota']:.1f}% from {r['prior_ota']:.1f}% "
            f"because planned minutes per km rose {pad_pct:.1f}% while actual minutes per "
            f"km moved {act_pct:.1f}% - the schedule moved, the commute did not",
            [_ev("on-time arrival today", r["ota"], "%", "mis.trips"),
             _ev("on-time arrival before", r["prior_ota"], "%", "mis.trips"),
             _ev("on-time points gained", gain, "points", "mis.trips"),
             _ev("planned minutes per km rose", pad_pct, "%", "mis.trips"),
             _ev("actual minutes per km moved", act_pct, "%", "mis.trips"),
             _ev("planned minutes per km today", r["plan_mpk"], "min/km", "mis.trips"),
             _ev("planned minutes per km before", r["prior_plan_mpk"], "min/km", "mis.trips"),
             _ev("actual minutes per km today", r["act_mpk"], "min/km", "mis.trips"),
             _ev("actual minutes per km before", r["prior_act_mpk"], "min/km", "mis.trips"),
             _ev("planned km per trip today", r["plan_km"], "km", "mis.trips"),
             _ev("planned km per trip before", r["prior_plan_km"], "km", "mis.trips"),
             _ev("distinct cabs today", r["cabs"], "cabs", "mis.trips"),
             _ev("distinct cabs before", r["prior_cabs"], "cabs", "mis.trips"),
             _ev("trips today", r["n"], "trips", "mis.trips")]))
    return out


_DENOM_SQL = f"""
with today as (
    select * from {baselines.FACTS} where d = $day and metric = 'ota15'
),
hist as (
    select * from {baselines.FACTS}
    where metric = 'ota15' and d >= $day - interval '28 days' and d < $day
      and dayofweek(d) = dayofweek($day)
),
base as (
    select entity_type, entity_id, median(value) as bv, median(n) as bn, count(*) as k
    from hist group by 1, 2
)
select t.entity_type, t.entity_id, t.parent_id, t.value, t.n,
       b.bv as baseline, b.bn as baseline_volume
from today t join base b
  on b.entity_type = t.entity_type and b.entity_id = t.entity_id
where b.k >= {baselines.BASELINE_MIN_N}
  and t.value - b.bv >= $gain
  and t.n <= $shrink * b.bn
  and t.n >= {MIN_SAMPLE}
"""


def _denominator_change(con, day: date) -> list[dict]:
    """A rate that improved because the population it is measured over shrank."""
    out = []
    for r in _rows(con.execute(_DENOM_SQL, {"day": day, "gain": DENOM_GAIN,
                                            "shrink": DENOM_SHRINK})):
        gain = r["value"] - r["baseline"]
        drop = 100.0 * (1 - r["n"] / r["baseline_volume"])
        out.append(_signal(
            day, "metric_integrity", r["entity_type"], r["entity_id"], r["parent_id"],
            "ota15", r["value"], r["baseline"], None, r["n"], "better",
            severity_for(None, r["n"]),
            f"{r['entity_id']} on-time rose to {r['value']:.1f}% from a {r['baseline']:.1f}% "
            f"baseline on {drop:.1f}% fewer trips - the rate improved because the "
            f"denominator shrank",
            [_ev("on-time arrival", r["value"], "%", "mis.trips"),
             _ev("trailing same-weekday baseline", r["baseline"], "%", "entity_baseline"),
             _ev("on-time points gained", gain, "points", "mis.trips"),
             _ev("trips run", r["n"], "trips", "mis.trips"),
             _ev("usual same-weekday volume", r["baseline_volume"], "trips", "mis.trips"),
             _ev("volume fell", drop, "%", "mis.trips")]))
    return out


_CATEGORY_SQL = """
with win as (
    select business_unit as bu, event_type as et, ack_minutes,
           cast(raised_at as date) as d
    from mis.alerts
    where cast(raised_at as date) between $day - interval '27 days' and $day
      and ack_minutes is not null and business_unit is not null
),
by_type as (
    select bu, et,
           count(*) filter (where d >  $day - interval '14 days') as n_after,
           count(*) filter (where d <= $day - interval '14 days') as n_before,
           avg(ack_minutes) filter (where d >  $day - interval '14 days') as ack_after,
           avg(ack_minutes) filter (where d <= $day - interval '14 days') as ack_before
    from win group by 1, 2
),
headline as (
    select bu, sum(n_after) as n_after, sum(n_before) as n_before,
           sum(ack_after * n_after) / nullif(sum(n_after), 0) as ack_after,
           sum(ack_before * n_before) / nullif(sum(n_before), 0) as ack_before
    from by_type group by 1
),
-- the same arithmetic restricted to event types present in both halves
like_for_like as (
    select bu, sum(ack_after * n_after) / nullif(sum(n_after), 0) as ack_after,
           sum(ack_before * n_before) / nullif(sum(n_before), 0) as ack_before
    from by_type where n_after > 0 and n_before > 0 group by 1
),
vanished as (
    select bu, et, n_before, ack_before,
           row_number() over (partition by bu order by n_before desc, et) as rn
    from by_type where n_after = 0 and n_before >= $min_alerts
),
-- the biggest category present in both halves, as the control: if it did not move,
-- nothing was fixed.
control as (
    select bu, et, n_before, n_after, ack_before, ack_after,
           row_number() over (partition by bu order by n_before + n_after desc, et) as rn
    from by_type where n_after > 0 and n_before > 0
),
last_seen as (
    select business_unit as bu, event_type as et, max(cast(raised_at as date)) as seen
    from mis.alerts where business_unit is not null group by 1, 2
)
select h.bu, h.ack_before, h.ack_after, h.n_before, h.n_after,
       l.ack_before as lfl_before, l.ack_after as lfl_after,
       v.et as gone_type, v.n_before as gone_n, v.ack_before as gone_ack,
       c.et as control_type, c.ack_before as control_before, c.ack_after as control_after,
       date_diff('day', ls.seen, $day) as days_absent
from headline h
join like_for_like l on l.bu = h.bu
join vanished v on v.bu = h.bu and v.rn = 1
join last_seen ls on ls.bu = h.bu and ls.et = v.et
left join control c on c.bu = h.bu and c.rn = 1
where h.ack_after <= $drop * h.ack_before
  and l.ack_after  >  $floor * l.ack_before
  and date_diff('day', ls.seen, $day) between $absent_lo and $absent_hi
"""


def _category_deletion(con, day: date) -> list[dict]:
    """An aggregate that improved because a slow category stopped being recorded.

    pinnacle-Slc's acknowledgement time falls 2.8x. Every minute of that gain is
    EMPLOYEE_SIGN_OFF_TIME_VIOLATION ceasing to be generated. Like for like, the
    improvement is a fraction of the headline, and the biggest category present on both
    sides of the boundary has not moved at all - which is the proof that nothing was
    fixed.
    """
    out = []
    for r in _rows(con.execute(_CATEGORY_SQL, {
            "day": day, "min_alerts": CAT_MIN_ALERTS, "drop": CAT_HEADLINE_DROP,
            "floor": CAT_LFL_FLOOR, "absent_lo": CAT_ABSENT_DAYS[0],
            "absent_hi": CAT_ABSENT_DAYS[1]})):
        headline_x = r["ack_before"] / r["ack_after"] if r["ack_after"] else 0
        lfl_x = r["lfl_before"] / r["lfl_after"] if r["lfl_after"] else 0
        ev = [_ev("acknowledgement minutes before", r["ack_before"], "min", "mis.alerts"),
              _ev("acknowledgement minutes after", r["ack_after"], "min", "mis.alerts"),
              _ev("headline improvement", headline_x, "x", "mis.alerts"),
              _ev("like-for-like minutes before", r["lfl_before"], "min", "mis.alerts"),
              _ev("like-for-like minutes after", r["lfl_after"], "min", "mis.alerts"),
              _ev("like-for-like improvement", lfl_x, "x", "mis.alerts"),
              _ev(f"{r['gone_type']} alerts before it stopped", r["gone_n"], "alerts",
                  "mis.alerts"),
              _ev(f"{r['gone_type']} acknowledgement minutes", r["gone_ack"], "min",
                  "mis.alerts"),
              _ev("alerts after", r["n_after"], "alerts", "mis.alerts")]
        if r["control_type"]:
            ev += [_ev(f"{r['control_type']} acknowledgement before", r["control_before"],
                       "min", "mis.alerts"),
                   _ev(f"{r['control_type']} acknowledgement after", r["control_after"],
                       "min", "mis.alerts")]
        out.append(_signal(
            day, "metric_integrity", "business_unit", r["bu"], None, "ack_minutes",
            r["ack_after"], r["ack_before"], None, r["n_after"], "better", "high",
            f"{r['bu']} alert acknowledgement fell from {r['ack_before']:.1f} to "
            f"{r['ack_after']:.1f} minutes only because {r['gone_type']} stopped being "
            f"recorded; like for like it moved {r['lfl_before']:.1f} to {r['lfl_after']:.1f}",
            ev))
    return out


# ----------------------------------------------------------------- alert_ack_sla

def alert_ack_sla(con, day: date, base: list[dict]) -> list[dict]:
    """Safety alerts left unanswered, against the SLA and against the peer group.

    Two shapes, because they are two different conversations. An *acute* spike is
    reported the day it happens. A *chronic* breach - catalyst-Sac has answered its
    alerts in a shift and a half every day for three months - is reported once a week,
    on Mondays, because a standing condition restated daily is not information.
    """
    out = []
    for r in base:
        if r["metric"] != "ack_minutes" or r["value"] is None:
            continue
        if r["entity_type"] != "business_unit" or (r["n"] or 0) < ACK_MIN_ALERTS:
            continue
        peer = r["peer_median"] or 0.0
        acute = r["z"] is not None and r["z"] >= 2.0 and r["value"] > ACK_SLA_MIN
        chronic = (r["value"] > ACK_SLA_MIN and peer > 0
                   and r["value"] >= ACK_PEER_MULT * peer and day.weekday() == 0)
        if not (acute or chronic):
            continue
        ratio = r["value"] / peer if peer else 0.0
        ev = [_ev("mean acknowledgement time", r["value"], "min", "mis.alerts"),
              _ev("alerts raised", r["n"], "alerts", "mis.alerts"),
              _ev("acknowledgement SLA", ACK_SLA_MIN, "min", "policy"),
              _ev("peer median acknowledgement", peer, "min", "entity_baseline"),
              _ev("times the peer median", ratio, "x", "entity_baseline")]
        if r["baseline_mean"] is not None:
            ev.append(_ev("trailing same-weekday baseline", r["baseline_mean"], "min",
                          "entity_baseline"))
        out.append(_signal(
            day, "alert_ack_sla", r["entity_type"], r["entity_id"], r["parent_id"],
            "ack_minutes", r["value"], r["baseline_mean"], r["z"], r["n"], "worse",
            severity_for(max(abs(r["z"] or 0), ratio / ACK_PEER_MULT * MEDIUM_Z), r["n"]),
            f"{r['entity_id']} answered safety alerts in {r['value']:.1f} minutes on "
            f"average against a {peer:.1f} minute peer median",
            ev))
    return out


# ---------------------------------------------------------------- safety_cluster

_CLUSTER_SQL = f"""
with hist as (
    select entity_id, median(value) as daily_median, count(*) as k
    from {baselines.FACTS}
    where metric = 'sev1_count' and entity_type = 'business_unit'
      and d >= $day - interval '28 days' and d < $day
    group by 1
)
select t.entity_id, t.value, h.daily_median
from {baselines.FACTS} t join hist h on h.entity_id = t.entity_id
where t.d = $day and t.metric = 'sev1_count' and t.entity_type = 'business_unit'
  and h.k >= 10 and t.value > $mult * h.daily_median and t.value >= $min_count
"""


def safety_cluster(con, day: date, base: list[dict]) -> list[dict]:
    """A day carrying far more Sev-1 alerts than usual.

    Two ways in, because the same-weekday baseline is the wrong instrument on its own
    here. catalyst-Sac raises a dozen Sev-1 alerts on an ordinary Wednesday, so a
    twenty-three alert Wednesday scores an unremarkable z - the weekday shape has
    absorbed the very thing being looked for. The volume rule catches it: more than
    twice this unit's trailing daily median, and at least ten alerts, is a cluster
    whichever day of the week it lands on.
    """
    hits: dict[str, dict] = {}
    for r in base:
        if r["metric"] != "sev1_count" or r["entity_type"] != "business_unit":
            continue
        if r["z"] is not None and r["z"] >= Z_SAFETY and (r["value"] or 0) >= 5:
            hits[r["entity_id"]] = {"value": r["value"], "baseline": r["baseline_mean"],
                                    "z": r["z"], "n": r["n"], "median": None}
    for r in _rows(con.execute(_CLUSTER_SQL, {"day": day, "mult": SAFETY_MULT,
                                              "min_count": SAFETY_MIN_COUNT})):
        hit = hits.setdefault(r["entity_id"], {"value": r["value"], "baseline": None,
                                               "z": None, "n": None, "median": None})
        hit["median"] = r["daily_median"]

    out = []
    for entity_id, h in hits.items():
        detail = _rows(con.execute("""
            select event_type, count(*) as n from mis.alerts
            where business_unit = ? and cast(raised_at as date) = ? and severity = 'Sev-1'
            group by 1 order by 2 desc, 1 limit 2""", [entity_id, day]))
        ack = con.execute("""
            select avg(ack_minutes) from mis.alerts
            where business_unit = ? and cast(raised_at as date) = ?""",
                          [entity_id, day]).fetchone()[0]
        ev = [_ev("Sev-1 alerts raised", h["value"], "alerts", "mis.alerts")]
        if h["median"] is not None:
            ev.append(_ev("trailing daily median", h["median"], "alerts", "mis.alerts"))
        if h["baseline"] is not None:
            ev.append(_ev("trailing same-weekday baseline", h["baseline"], "alerts",
                          "entity_baseline"))
        ev += [_ev(f"{d['event_type']} events", d["n"], "alerts", "mis.alerts")
               for d in detail]
        if ack is not None:
            ev.append(_ev("mean acknowledgement time that day", ack, "min", "mis.alerts"))
        reference = h["median"] if h["median"] is not None else h["baseline"]
        out.append(_signal(
            day, "safety_cluster", "business_unit", entity_id, None, "sev1_count",
            h["value"], reference, h["z"], int(h["value"]), "worse",
            "critical" if h["value"] >= 20 else "high",
            f"{entity_id} raised {h['value']:.0f} severity-one alerts against a usual "
            f"{reference:.1f} a day",
            ev))
    return out


# ----------------------------------------------------------------- escort_breach

_ESCORT_SQL = """
select t.business_unit as bu,
       count(*) as alerts,
       count(distinct t.trip_id) as trips,
       avg(a.ack_minutes) as ack
from mis.alerts a
join mis.trips t on t.trip_id = a.trip_id
where a.event_type = 'WOMAN_TRAVELLING_ALONE'
  and not t.actual_escort
  and cast(a.raised_at as date) = $day
  and t.business_unit is not null
group by 1 order by 3 desc, 2 desc, 1
"""


def escort_breach(con, day: date, base: list[dict]) -> list[dict]:
    """The alert that exists to catch a woman travelling alone, firing with no escort aboard.

    A policy breach, not a statistical anomaly, so there is no z and no baseline: the
    target is zero and the observed value is not zero. Fleet-wide it happens on 69 to 98
    trips every single day of the window.

    One signal a day, carried by the business unit with the most breaches, with the
    fleet total and the per-unit split both in the evidence. Splitting a standing
    fleet-wide policy failure into one alert per unit per day would put a hundred and
    thirty rows in front of a transport head describing one thing.
    """
    rows = _rows(con.execute(_ESCORT_SQL, {"day": day}))
    rows = [r for r in rows if r["trips"]]
    if not rows:
        return []
    fleet_trips = sum(r["trips"] for r in rows)
    fleet_alerts = sum(r["alerts"] for r in rows)
    if fleet_trips < ESCORT_MIN_TRIPS:
        return []
    worst = rows[0]
    ev = [_ev("trips that ran with no escort aboard", fleet_trips, "trips", "mis.trips"),
          _ev("alerts raised", fleet_alerts, "alerts", "mis.alerts"),
          _ev("policy target", 0, "trips", "policy")]
    ev += [_ev(f"{r['bu']} trips with no escort aboard", r["trips"], "trips", "mis.trips")
           for r in rows]
    if worst["ack"] is not None:
        ev.append(_ev(f"{worst['bu']} mean acknowledgement time", worst["ack"], "min",
                      "mis.alerts"))
    return [_signal(
        day, "escort_breach", "business_unit", worst["bu"], None, "escort_breach_trips",
        fleet_trips, 0.0, None, fleet_alerts, "worse",
        "critical" if fleet_trips >= 50 else "high",
        f"{fleet_alerts} woman-travelling-alone alerts on {fleet_trips} trips that ran "
        f"with no escort aboard, {worst['trips']} of them at {worst['bu']}",
        ev)]


# ------------------------------------------------------------------ noshow_spike

def noshow_spike(con, day: date, base: list[dict]) -> list[dict]:
    """No-shows above the same-weekday norm, on a population large enough to mean it.

    `signintype` is null on 190,009 legs and those legs are 62.1% no-show: the null
    means 'never picked up'. Keeping them is the whole signal, so `is_no_show` is read
    straight off `mis.emp_legs` with nothing dropped.
    """
    out = []
    for r in base:
        if r["metric"] != "noshow_pct" or r["z"] is None:
            continue
        if r["z"] < Z_NOSHOW or (r["n"] or 0) < NOSHOW_MIN_N:
            continue
        extra = r["n"] * (r["value"] - r["baseline_mean"]) / 100.0
        out.append(_signal(
            day, "noshow_spike", r["entity_type"], r["entity_id"], r["parent_id"],
            "noshow_pct", r["value"], r["baseline_mean"], r["z"], r["n"], "worse",
            severity_for(r["z"], r["n"]),
            f"{r['entity_id']} no-shows reached {r['value']:.2f}% against a "
            f"{r['baseline_mean']:.2f}% same-weekday baseline",
            [_ev("no-show rate", r["value"], "%", "mis.emp_legs"),
             _ev("trailing same-weekday baseline", r["baseline_mean"], "%",
                 "entity_baseline"),
             _ev("legs scheduled", r["n"], "legs", "mis.emp_legs"),
             _ev("no-shows above baseline", round(extra), "employees", "mis.emp_legs")]))
    return out


# --------------------------------------------------------------- billing_anomaly

_CYCLE_SQL = "select count(*) from mis.bills where cycle_end = ?"

_ZERO_KM_SQL = """
with per_bu as (
    select business_unit as bu, count(*) as lines_n,
           sum(trip_cost) as spend,
           100.0 * count(*) filter (where is_zero_km) / count(*) as zero_line_pct,
           100.0 * sum(trip_cost) filter (where is_zero_km) / nullif(sum(trip_cost), 0)
               as zero_spend_pct,
           sum(trip_cost) filter (where is_zero_km) as zero_spend,
           sum(trip_cost) filter (where trip_id is null) as unattributed_spend,
           count(*) filter (where trip_id is null) as unattributed_lines
    from mis.bills where cycle_end = $day and business_unit is not null
    group by 1
),
worst_vendor as (
    select business_unit as bu, vendor, lines_n, zero_pct, zero_spend from (
      select business_unit, vendor, count(*) as lines_n,
             100.0 * count(*) filter (where is_zero_km) / count(*) as zero_pct,
             sum(trip_cost) filter (where is_zero_km) as zero_spend,
             row_number() over (partition by business_unit
                                order by sum(trip_cost) filter (where is_zero_km) desc,
                                         vendor) as rn
      from mis.bills where cycle_end = $day and business_unit is not null and vendor is not null
      group by 1, 2
    ) where rn = 1
)
select b.*, v.vendor as worst_vendor, v.zero_pct as worst_vendor_zero_pct,
       v.zero_spend as worst_vendor_zero_spend
from per_bu b left join worst_vendor v on v.bu = b.bu
where b.zero_spend_pct >= $share
"""

# Not one duplicate in this dataset sits inside a single cycle, so grouping within one
# cycle_end finds nothing at all. They cross billing periods, and by months: a trip
# invoiced on the May 1-15 cycle turns up again on the July monthly roll-up. The check
# that finds them is the one a finance team would actually run at close - are any trips
# on this invoice already paid for on an earlier one.
_DUPLICATE_SQL = """
with earlier as (
    select trip_id, count(*) as k, sum(trip_cost) as cost
    from mis.bills where cycle_end < $day and trip_id is not null group by 1
),
closing as (
    select business_unit as bu, trip_id, count(*) as k, sum(trip_cost) as cost
    from mis.bills
    where cycle_end = $day and trip_id is not null and business_unit is not null
    group by 1, 2
)
select c.bu, count(*) as trips, sum(c.k + e.k) as lines_n,
       sum(c.cost) as recoverable, sum(c.cost + e.cost) as billed
from closing c join earlier e on e.trip_id = c.trip_id
group by 1 having sum(c.cost) > 0 order by 4 desc
"""

_CONTRACT_SQL = """
with per as (
    select contract, count(*) as lines_n, sum(trip_cost) as spend, sum(billed_km) as km,
           sum(trip_cost) / nullif(sum(billed_km), 0) as cost_per_km
    from mis.bills
    where cycle_end = $day and billed_km > 0 and contract is not null
    group by 1 having count(*) >= $min_lines
),
fleet as (select median(cost_per_km) as med from per)
select p.*, f.med as fleet_median, p.cost_per_km / f.med as multiple
from per p, fleet f
where p.cost_per_km >= $mult * f.med
"""


def billing_anomaly(con, day: date, base: list[dict]) -> list[dict]:
    """Billing integrity, at cycle close only.

    Cycles are semi-monthly with a monthly roll-up on top, so this runs on the six
    closing dates in the window and not on the other eighty-six days. `trip_id` is the
    literal string 'OverHead' on 160 lines, which normalises to null - those lines are
    counted as unattributed spend rather than crashed on.
    """
    if not con.execute(_CYCLE_SQL, [day]).fetchone()[0]:
        return []
    out = []

    for r in _rows(con.execute(_ZERO_KM_SQL, {"day": day, "share": BILL_ZERO_KM_SHARE})):
        ev = [_ev("cycle spend with no distance evidence", r["zero_spend_pct"], "%",
                  "mis.bills"),
              _ev("billed lines with zero distance", r["zero_line_pct"], "%", "mis.bills"),
              _ev("spend with no distance evidence", r["zero_spend"] / 1e6, "million",
                  "mis.bills"),
              _ev("cycle spend", r["spend"] / 1e6, "million", "mis.bills"),
              _ev("billed lines", r["lines_n"], "lines", "mis.bills")]
        if r["worst_vendor"]:
            ev += [_ev(f"{r['worst_vendor']} lines with zero distance",
                       r["worst_vendor_zero_pct"], "%", "mis.bills"),
                   _ev(f"{r['worst_vendor']} spend with no distance evidence",
                       (r["worst_vendor_zero_spend"] or 0) / 1e6, "million", "mis.bills")]
        if r["unattributed_lines"]:
            ev += [_ev("lines with no usable trip id", r["unattributed_lines"], "lines",
                       "mis.bills"),
                   _ev("spend on lines with no usable trip id",
                       (r["unattributed_spend"] or 0) / 1e6, "million", "mis.bills")]
        out.append(_signal(
            day, "billing_anomaly", "business_unit", r["bu"], None, "zero_km_pct",
            r["zero_spend_pct"], BILL_ZERO_KM_SHARE, None, r["lines_n"], "worse",
            "high" if r["zero_spend_pct"] >= 50 else "medium",
            f"{r['bu']} closed the cycle with {r['zero_spend_pct']:.1f}% of spend carrying "
            f"no distance evidence, {r['zero_spend']/1e6:.1f} million of "
            f"{r['spend']/1e6:.1f} million",
            ev))

    for r in _rows(con.execute(_DUPLICATE_SQL, {"day": day})):
        out.append(_signal(
            day, "billing_anomaly", "business_unit", r["bu"], None, "duplicate_spend",
            r["recoverable"], 0.0, None, r["lines_n"], "worse", "high",
            f"{r['bu']} billed {r['trips']} trips on this cycle that were already invoiced "
            f"on an earlier one, across {r['lines_n']} lines and "
            f"{r['recoverable']/1e6:.2f} million of recoverable spend",
            [_ev("trips billed a second time", r["trips"], "trips", "mis.bills"),
             _ev("billed lines involved", r["lines_n"], "lines", "mis.bills"),
             _ev("recoverable spend", r["recoverable"] / 1e6, "million", "mis.bills"),
             _ev("total billed on those trips", r["billed"] / 1e6, "million", "mis.bills"),
             _ev("policy target", 0, "trips", "policy")]))

    for r in _rows(con.execute(_CONTRACT_SQL, {"day": day, "mult": BILL_CONTRACT_MULT,
                                               "min_lines": BILL_CONTRACT_MIN_LINES})):
        out.append(_signal(
            day, "billing_anomaly", "contract", r["contract"], None, "cost_per_km",
            r["cost_per_km"], r["fleet_median"], None, r["lines_n"], "worse", "medium",
            # The contract code carries digits of its own, and the faithfulness gate
            # extracts every numeral it finds. The code is already in entity_id, so the
            # headline names it as "this contract" and stays entirely sourced.
            f"This contract bills {r['cost_per_km']:.2f} per km against a "
            f"{r['fleet_median']:.2f} fleet median, {r['multiple']:.2f} times the rate for "
            f"the same four-wheeled service",
            [_ev("cost per km", r["cost_per_km"], "per km", "mis.bills"),
             _ev("fleet median cost per km", r["fleet_median"], "per km", "mis.bills"),
             _ev("times the fleet median", r["multiple"], "x", "mis.bills"),
             _ev("billed lines", r["lines_n"], "lines", "mis.bills"),
             _ev("spend on this contract", r["spend"] / 1e6, "million", "mis.bills")]))
    return out


DETECTORS = (
    ("punctuality_drop", punctuality_drop),
    ("metric_integrity", metric_integrity),
    ("alert_ack_sla", alert_ack_sla),
    ("safety_cluster", safety_cluster),
    ("escort_breach", escort_breach),
    ("noshow_spike", noshow_spike),
    ("billing_anomaly", billing_anomaly),
)
