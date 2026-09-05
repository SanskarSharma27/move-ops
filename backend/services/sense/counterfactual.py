"""The precomputed what-if grid. reason/ reads this; it never recomputes a metric.

Three levers, in descending order of how much they can be trusted, and the grid says
which is which in the `confidence` column:

    schedule_pad_min   exact       the same trips, recounted against a later finish line
    vendor_substitute  estimated   one vendor's observed rate applied to another's volume
                       weak        ... when the observed rate rests on too few trips
    fleet_add          estimated   load per vehicle, assumed to move punctuality

`assumption` is mandatory on every row and it is where the honesty lives. Schedule
padding is the sharpest case: it is *exactly* computable and it is also exactly the
trick Santa Clara pulled on 19 July. An agent that recommends adding ten minutes to the
plan without saying that this moves the metric and not the commute has failed its own
metric-integrity test, so every `schedule_pad_min` row carries that sentence.

Cadence: the grid is rebuilt on the 7th, 14th, 21st and 28th of each month and on the
last day of the month, over the calendar month to date. Daily rebuilds would multiply
the table by seven and change nothing anybody reads, and a month-to-date window is what
an operations manager means by "our on-time this month".
"""
from __future__ import annotations

import logging
import math
from calendar import monthrange
from datetime import date

from common import upsert

log = logging.getLogger("sense.counterfactual")

PAD_MINUTES = (5, 10, 15)
ONTIME_SECONDS = 15 * 60          # the repo-wide definition: actual_end - planned_end <= 15 min

REBUILD_DAYS = (7, 14, 21, 28)
PAD_MIN_TRIPS = 200               # an office too small for this cannot support a projection
SUB_MIN_SITE_TRIPS = 500
SUB_MIN_CANDIDATE_TRIPS = 20
SUB_WEAK_BELOW = 200              # PRD: weak if n < 200, else estimated

KEY = ("as_of", "entity_type", "entity_id", "lever", "param", "metric")
COLUMNS = ("as_of", "entity_type", "entity_id", "lever", "param", "metric",
           "baseline_value", "projected_value", "delta", "n", "assumption", "confidence")

PAD_CAVEAT = (
    "This moves the metric, not the commute: the same journeys arrive at the same time, "
    "against a target set later. It is the identical change Santa Clara made on the "
    "nineteenth of July, which lifted its on-time by sixteen points while actual journey "
    "time stood still. Recommend only alongside a real journey-time fix, and never as a "
    "way of reporting an improvement."
)


def is_rebuild_day(day: date) -> bool:
    return day.day in REBUILD_DAYS or day.day == monthrange(day.year, day.month)[1]


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row(as_of, entity_type, entity_id, lever, param, metric,
         baseline, projected, n, assumption, confidence) -> dict:
    return {
        "as_of": as_of, "entity_type": entity_type, "entity_id": entity_id,
        "lever": lever, "param": str(param), "metric": metric,
        "baseline_value": round(float(baseline), 4),
        "projected_value": round(float(projected), 4),
        "delta": round(float(projected) - float(baseline), 4),
        "n": int(n), "assumption": assumption, "confidence": confidence,
    }


def build_day(con, day: date) -> int:
    """Rebuild the grid for one as_of, if this is one of the rebuild days."""
    if not is_rebuild_day(day):
        return 0
    start = day.replace(day=1)
    rows = (_schedule_pad(con, start, day)
            + _vendor_substitute(con, start, day)
            + _fleet_add(con, start, day))
    n = upsert(con, "counterfactual", rows, key=KEY)
    log.debug("counterfactual %s: %d rows", day, n)
    return n


# ------------------------------------------------------------ schedule_pad_min

_PAD_SQL = f"""
with scoped as (
    select business_unit, office, actual_end - planned_end as slip
    from mis.trips
    where trip_date between $start and $day and business_unit is not null
),
by_bu as (
    select 'business_unit' as entity_type, business_unit as entity_id, count(*) as n,
           100.0 * avg(case when slip <= {ONTIME_SECONDS} then 1.0 else 0.0 end) as base,
           {{pads}}
    from scoped group by 1, 2
),
by_office as (
    select 'office' as entity_type, business_unit || ' / ' || office as entity_id,
           count(*) as n,
           100.0 * avg(case when slip <= {ONTIME_SECONDS} then 1.0 else 0.0 end) as base,
           {{pads}}
    from scoped where office is not null group by 1, 2
)
select * from by_bu where n >= $min_trips
union all
select * from by_office where n >= $min_trips
"""

_PAD_EXPR = ", ".join(
    f"100.0 * avg(case when slip <= {ONTIME_SECONDS + m * 60} then 1.0 else 0.0 end) as pad{m}"
    for m in PAD_MINUTES)


def _schedule_pad(con, start: date, day: date) -> list[dict]:
    """Exact. The same trips, recounted against a finish line N minutes later.

    Nothing is modelled: `is_ontime_15` is `actual_end - planned_end <= 15 min`, so
    shifting the target is a different comparison over identical rows. vanta-Aus in July
    reads 81.5% as scheduled, 86.7% at five minutes and 90.4% at ten, over 23,584 trips.
    """
    sql = _PAD_SQL.replace("{pads}", _PAD_EXPR)
    out = []
    for r in _rows(con.execute(sql, {"start": start, "day": day,
                                     "min_trips": PAD_MIN_TRIPS})):
        for m in PAD_MINUTES:
            out.append(_row(
                day, r["entity_type"], r["entity_id"], "schedule_pad_min", m, "ota15",
                r["base"], r[f"pad{m}"], r["n"],
                f"Recomputed against the same {r['n']} trips with planned_end shifted "
                f"{m} minutes later; no trip, route or vehicle changes. " + PAD_CAVEAT,
                "exact"))
    return out


# ----------------------------------------------------------- vendor_substitute

_SUB_SQL = """
with scoped as (
    select business_unit || ' / ' || office as site, business_unit as parent, vendor,
           case when is_ontime_15 then 1.0 else 0.0 end as ontime
    from mis.trips
    where trip_date between $start and $day
      and business_unit is not null and office is not null and vendor is not null
),
per_vendor as (
    select site, parent, vendor, count(*) as n, 100.0 * avg(ontime) as ota
    from scoped group by 1, 2, 3
),
per_site as (
    select site, sum(n) as site_n, sum(ota * n) / sum(n) as site_ota
    from per_vendor group by 1
),
incumbent as (
    select site, vendor, n, ota from (
        select site, vendor, n, ota,
               row_number() over (partition by site order by n desc, vendor) as rn
        from per_vendor
    ) where rn = 1
)
select v.site, v.parent, v.vendor as candidate, v.n as candidate_n, v.ota as candidate_ota,
       i.vendor as incumbent, i.n as incumbent_n, i.ota as incumbent_ota,
       s.site_n, s.site_ota
from per_vendor v
join incumbent i on i.site = v.site
join per_site s on s.site = v.site
where s.site_n >= $min_site and v.vendor <> i.vendor and v.n >= $min_candidate
"""


def _vendor_substitute(con, start: date, day: date) -> list[dict]:
    """Apply a candidate vendor's observed on-time at this site to the incumbent's volume.

    An estimate, and labelled one. The candidate's rate is real but it was earned on the
    candidate's own trips - its shifts, its routes, its share of the site - and there is
    no guarantee it survives being handed four times the volume. Where that rate rests
    on fewer than 200 trips the row is marked `weak`, because Santa Clara's most
    tempting substitution is built on 60 trips and quoting it as a target would be the
    same error this service exists to catch.
    """
    out = []
    for r in _rows(con.execute(_SUB_SQL, {"start": start, "day": day,
                                          "min_site": SUB_MIN_SITE_TRIPS,
                                          "min_candidate": SUB_MIN_CANDIDATE_TRIPS})):
        others_n = r["site_n"] - r["incumbent_n"]
        others_ontime = r["site_ota"] * r["site_n"] / 100.0 - r["incumbent_ota"] * r["incumbent_n"] / 100.0
        projected = 100.0 * (others_ontime + r["candidate_ota"] * r["incumbent_n"] / 100.0) / r["site_n"]
        weak = r["candidate_n"] < SUB_WEAK_BELOW
        better = r["candidate_ota"] > r["incumbent_ota"]
        assumption = (
            f"Applies {r['candidate']}'s observed {r['candidate_ota']:.1f}% on-time at this "
            f"site to {r['incumbent']}'s {r['incumbent_n']} trips, leaving the other "
            f"{others_n} trips as they ran. ")
        if weak:
            assumption += (
                f"{r['candidate']} ran only {r['candidate_n']} trips here, far too few to "
                f"treat that rate as a capability. This projection is weak and must not be "
                f"quoted as a target.")
        elif better:
            assumption += (
                f"Assumes the rate survives {r['incumbent_n']} trips of extra volume, which "
                f"the data cannot confirm - no vendor-site pair starts or stops in this "
                f"window, so there is no substitution anywhere in the history to learn from.")
        else:
            assumption += (
                f"{r['candidate']} is no better than the vendor it would replace, so this "
                f"substitution is not worth making. Recorded so the question has an answer.")
        out.append(_row(
            day, "office", r["site"], "vendor_substitute", r["candidate"], "ota15",
            r["site_ota"], projected, r["site_n"], assumption,
            "weak" if weak else "estimated"))
    return out


# ------------------------------------------------------------------- fleet_add

_FLEET_SQL = """
with monthly as (
    select business_unit as bu, date_trunc('month', trip_date) as m,
           count(*) as trips, count(distinct actual_cab_registration) as cabs,
           100.0 * avg(case when is_ontime_15 then 1.0 else 0.0 end) as ota
    from mis.trips where trip_date <= $day and business_unit is not null group by 1, 2
),
current as (select * from monthly where m = date_trunc('month', $day::date)),
-- the best month this unit has actually achieved, which is the only evidence there is
-- for what it could achieve again
best as (
    select bu, m, trips, cabs, ota, trips * 1.0 / cabs as tpc from (
        select *, row_number() over (partition by bu order by ota desc, m) as rn
        from monthly where m < date_trunc('month', $day::date) and cabs > 0
    ) where rn = 1
)
select c.bu, c.trips, c.cabs, c.ota, c.trips * 1.0 / c.cabs as tpc,
       b.m as best_month, b.ota as best_ota, b.tpc as best_tpc, b.cabs as best_cabs
from current c join best b on b.bu = c.bu
where c.cabs > 0 and b.ota > c.ota and b.tpc < c.trips * 1.0 / c.cabs
"""


def _fleet_add(con, start: date, day: date) -> list[dict]:
    """How many more cabs it would take to get back to the load this unit ran at its best.

    Estimated, and the assumption is doing real work. Punctuality tracked load per
    vehicle across these three months - pinnacle-Slc took 17% more trips on the same
    fleet and lost 7.8 points, then recovered when volume flattened - but a correlation
    over three months on five units is not a causal law, and the recovery happened on a
    *smaller* fleet, which says the system also re-planned around the new load. Both
    halves of that go in the assumption.
    """
    out = []
    for r in _rows(con.execute(_FLEET_SQL, {"day": day})):
        needed = math.ceil(r["trips"] / r["best_tpc"])
        deficit = needed - r["cabs"]
        if deficit <= 0:
            continue
        out.append(_row(
            day, "business_unit", r["bu"], "fleet_add", deficit, "ota15",
            r["ota"], r["best_ota"], r["trips"],
            f"At the {r['best_tpc']:.1f} trips-per-cab this unit ran in its best month, "
            f"{r['trips']} trips this month would need {needed} cabs against the "
            f"{r['cabs']} actually run. Assumes punctuality scales with load per vehicle, "
            f"which held across all three months here but is not proven causal - and note "
            f"that pinnacle-Slc recovered on a smaller fleet, so re-planning around the "
            f"load is a live alternative to buying capacity.",
            "estimated"))
    return out


def read(con, entity_type: str | None = None, entity_id: str | None = None,
         lever: str | None = None) -> list[dict]:
    sql = "select " + ", ".join(COLUMNS) + " from counterfactual where 1 = 1"
    params: list = []
    for col, val in (("entity_type", entity_type), ("entity_id", entity_id),
                     ("lever", lever)):
        if val:
            sql += f" and {col} = ?"
            params.append(val)
    sql += " order by as_of desc, entity_id, lever, param"
    return [dict(zip(COLUMNS, r)) for r in con.execute(sql, params).fetchall()]
