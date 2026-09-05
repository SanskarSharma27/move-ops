"""Weekday-aware rolling context. One row per (as_of, entity_type, entity_id, metric).

The single most important line in this service:

    the baseline is trailing 28 days, SAME WEEKDAY ONLY

Fleet on-time is 96% on Sundays and 60% on Tuesdays. A naive trailing mean therefore
reports a punctuality triumph every Sunday and a collapse every Tuesday, and six of
those false positives are sitting in this dataset already. Comparing Tuesdays to
Tuesdays deletes all of them without deleting a single real event.

Two further choices worth stating, because both change what fires:

*Robust centre and scale.* Twenty-eight days of one weekday is four observations. One
prior outlier drags the mean toward itself and inflates the standard deviation, so the
*next* occurrence of the same problem scores as normal - exactly the wrong behaviour
for a recurring incident. `baseline_mean` is therefore the median of those four values
and `baseline_sd` is 1.4826 x their median absolute deviation, floored per metric so a
very stable series cannot manufacture a huge z from a rounding wobble.

*Entity identity.* An office is always `"business_unit / office"`. Cedar Ridge Office
exists under `vanta-Aus` at 85.1% and under `orbit-Slc` at 71.0%; merging them invents
a site that does not exist. A vendor keeps its bare name, because that is the entity a
transport manager penalises, and `parent_id` carries the business unit holding most of
its volume - which is what lets reason/ fold two vendor alerts into one site incident.
"""
from __future__ import annotations

import logging
from datetime import date

from common import upsert

log = logging.getLogger("sense.baselines")

FACTS = "sense_daily_facts"
PARENTS = "sense_vendor_parent"

# metric -> (unit, floor under the robust scale, whether a rise is an improvement)
METRICS: dict[str, tuple[str, float, bool]] = {
    "ota15": ("%", 2.0, True),
    "ack_minutes": ("min", 5.0, False),
    "noshow_pct": ("%", 0.3, False),
    "seat_util": ("ratio", 0.02, True),
    "sev1_count": ("alerts", 1.0, False),
}

BASELINE_MIN_N = 3      # fewer trailing same-weekday observations than this: no z, no signal
PEER_MIN_N = 3          # a percentile over two entities is not a percentile


def unit_of(metric: str) -> str:
    return METRICS.get(metric, ("", 1.0, False))[0]


def higher_is_better(metric: str) -> bool:
    return METRICS.get(metric, ("", 1.0, False))[2]


# ------------------------------------------------------------------ daily facts

def ensure_facts(con) -> None:
    """Materialise the daily entity x metric grid once per connection.

    Temp tables, so this writes nothing to agent.duckdb and owns nobody else's data.
    Roughly 20k rows for the whole 92-day window, which turns every per-day baseline
    query into a scan of a few thousand rows instead of a scan of 615,546 trips.
    """
    try:
        con.execute(f"select 1 from {FACTS} limit 1")
        return
    except Exception:
        pass
    log.info("materialising %s ...", FACTS)
    con.execute(f"""
        create or replace temp table {PARENTS} as
        select vendor, business_unit as parent_id from (
          select vendor, business_unit,
                 row_number() over (partition by vendor
                                    order by count(*) desc, business_unit) rn
          from mis.trips where vendor is not null group by 1, 2
        ) where rn = 1
    """)
    con.execute(f"create or replace temp table {FACTS} as {_FACTS_SQL}")
    n, days = con.execute(
        f"select count(*), count(distinct d) from {FACTS}").fetchone()
    log.info("%s: %d rows over %d days", FACTS, n, days)


# `is_ontime_15` is precomputed on mis.trips as actual_end - planned_end <= 15 min.
# delay_reason and delay_minutes are quarantined and appear nowhere below.
_FACTS_SQL = f"""
with trip_grain as (
    select trip_date as d, 'business_unit' as entity_type, business_unit as entity_id,
           cast(null as varchar) as parent_id,
           100.0 * avg(case when is_ontime_15 then 1.0 else 0.0 end) as ota15,
           count(*) as n_trips, avg(seat_util) as seat_util, count(seat_util) as n_seat
    from mis.trips where business_unit is not null group by 1, 2, 3, 4
  union all
    select trip_date, 'office', business_unit || ' / ' || office, business_unit,
           100.0 * avg(case when is_ontime_15 then 1.0 else 0.0 end),
           count(*), avg(seat_util), count(seat_util)
    from mis.trips where business_unit is not null and office is not null group by 1, 2, 3, 4
  union all
    select t.trip_date, 'vendor', t.vendor, p.parent_id,
           100.0 * avg(case when t.is_ontime_15 then 1.0 else 0.0 end),
           count(*), avg(t.seat_util), count(t.seat_util)
    from mis.trips t left join {PARENTS} p on p.vendor = t.vendor
    where t.vendor is not null group by 1, 2, 3, 4
),
leg_grain as (
    select trip_date as d, 'business_unit' as entity_type, business_unit as entity_id,
           cast(null as varchar) as parent_id,
           100.0 * avg(case when is_no_show then 1.0 else 0.0 end) as noshow_pct,
           count(*) as n_legs
    from mis.emp_legs where business_unit is not null group by 1, 2, 3, 4
  union all
    select trip_date, 'office', business_unit || ' / ' || office, business_unit,
           100.0 * avg(case when is_no_show then 1.0 else 0.0 end), count(*)
    from mis.emp_legs where business_unit is not null and office is not null group by 1, 2, 3, 4
  union all
    select e.trip_date, 'vendor', t.vendor, p.parent_id,
           100.0 * avg(case when e.is_no_show then 1.0 else 0.0 end), count(*)
    from mis.emp_legs e
    join mis.trips t on t.trip_id = e.trip_id
    left join {PARENTS} p on p.vendor = t.vendor
    where t.vendor is not null group by 1, 2, 3, 4
),
alert_grain as (
    select cast(raised_at as date) as d, 'business_unit' as entity_type,
           business_unit as entity_id, cast(null as varchar) as parent_id,
           avg(ack_minutes) as ack_minutes, count(ack_minutes) as n_acked,
           cast(count(*) filter (where severity = 'Sev-1') as double) as sev1_count,
           count(*) as n_alerts
    from mis.alerts where business_unit is not null and raised_at is not null
    group by 1, 2, 3, 4
  union all
    select cast(a.raised_at as date), 'office', t.business_unit || ' / ' || t.office,
           t.business_unit, avg(a.ack_minutes), count(a.ack_minutes),
           cast(count(*) filter (where a.severity = 'Sev-1') as double), count(*)
    from mis.alerts a join mis.trips t on t.trip_id = a.trip_id
    where a.raised_at is not null and t.office is not null group by 1, 2, 3, 4
  union all
    select cast(a.raised_at as date), 'vendor', t.vendor, p.parent_id,
           avg(a.ack_minutes), count(a.ack_minutes),
           cast(count(*) filter (where a.severity = 'Sev-1') as double), count(*)
    from mis.alerts a join mis.trips t on t.trip_id = a.trip_id
    left join {PARENTS} p on p.vendor = t.vendor
    where a.raised_at is not null and t.vendor is not null group by 1, 2, 3, 4
)
    select d, entity_type, entity_id, parent_id, 'ota15' as metric,
           ota15 as value, n_trips as n
    from trip_grain where n_trips > 0
  union all
    select d, entity_type, entity_id, parent_id, 'seat_util', seat_util, n_seat
    from trip_grain where seat_util is not null
  union all
    select d, entity_type, entity_id, parent_id, 'noshow_pct', noshow_pct, n_legs
    from leg_grain where n_legs > 0
  union all
    select d, entity_type, entity_id, parent_id, 'ack_minutes', ack_minutes, n_acked
    from alert_grain where n_acked > 0
  union all
    select d, entity_type, entity_id, parent_id, 'sev1_count', sev1_count, n_alerts
    from alert_grain where n_alerts > 0
"""


def facts_for(con, day: date, metric: str) -> list[dict]:
    """The raw daily grid for one date and metric. Detectors read this."""
    ensure_facts(con)
    cur = con.execute(
        f"select d, entity_type, entity_id, parent_id, metric, value, n "
        f"from {FACTS} where d = ? and metric = ?", [day, metric])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------- baselines

_SD_FLOOR_CASE = "case metric " + " ".join(
    f"when '{m}' then {cfg[1]}" for m, cfg in METRICS.items()) + " else 1.0 end"

_BASELINE_SQL = f"""
with today as (
    select * from {FACTS} where d = $as_of
),
hist as (
    select * from {FACTS}
    where d >= $as_of - interval '28 days' and d < $as_of
      and dayofweek(d) = dayofweek($as_of)
),
centre as (
    select entity_type, entity_id, metric,
           median(value) as med, stddev_samp(value) as sd, count(*) as bn
    from hist group by 1, 2, 3
),
spread as (
    select h.entity_type, h.entity_id, h.metric,
           median(abs(h.value - c.med)) as mad
    from hist h join centre c
      on c.entity_type = h.entity_type and c.entity_id = h.entity_id and c.metric = h.metric
    group by 1, 2, 3
),
trend as (
    select entity_type, entity_id, metric,
           regr_slope(value, date_diff('day', date '2026-01-01', d)) as slope_28d
    from {FACTS}
    where d > $as_of - interval '28 days' and d <= $as_of
    group by 1, 2, 3 having count(*) >= 5
),
joined as (
    select t.d as as_of, t.entity_type, t.entity_id, t.parent_id, t.metric,
           t.value, t.n,
           c.med as baseline_mean, c.bn as baseline_n,
           greatest(coalesce(nullif(1.4826 * s.mad, 0), c.sd, 0),
                    {_SD_FLOOR_CASE}) as baseline_sd,
           r.slope_28d,
           case t.entity_type
                when 'business_unit' then 'business units'
                when 'office' then coalesce(t.parent_id, '?') || ' offices'
                when 'vendor' then coalesce(t.parent_id, '?') || ' vendors'
           end as peer_group
    from today t
    left join centre c
      on c.entity_type = t.entity_type and c.entity_id = t.entity_id and c.metric = t.metric
    left join spread s
      on s.entity_type = t.entity_type and s.entity_id = t.entity_id and s.metric = t.metric
    left join trend r
      on r.entity_type = t.entity_type and r.entity_id = t.entity_id and r.metric = t.metric
),
peer as (
    select metric, peer_group, median(value) as peer_median, count(*) as peer_n
    from joined group by 1, 2
)
select j.as_of, j.entity_type, j.entity_id, j.parent_id, j.metric,
       j.value, j.n, j.baseline_mean, j.baseline_sd, j.baseline_n,
       case when j.baseline_n >= {BASELINE_MIN_N} and j.baseline_sd > 0
            then (j.value - j.baseline_mean) / j.baseline_sd end as z,
       j.peer_group, p.peer_median,
       case when p.peer_n >= {PEER_MIN_N}
            then 100.0 * percent_rank() over (partition by j.metric, j.peer_group
                                              order by j.value) end as peer_pctile,
       j.slope_28d
from joined j
join peer p on p.metric = j.metric and p.peer_group = j.peer_group
"""

COLUMNS = ("as_of", "entity_type", "entity_id", "parent_id", "metric", "value", "n",
           "baseline_mean", "baseline_sd", "baseline_n", "z", "peer_group",
           "peer_median", "peer_pctile", "slope_28d")


def compute(con, day: date) -> list[dict]:
    """Every entity x metric baseline row for one business date."""
    ensure_facts(con)
    cur = con.execute(_BASELINE_SQL, {"as_of": day})
    rows = [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
    for r in rows:
        for k in ("value", "baseline_mean", "baseline_sd", "z",
                  "peer_median", "peer_pctile", "slope_28d"):
            if r[k] is not None:
                r[k] = round(float(r[k]), 4)
    return rows


def build_day(con, day: date) -> int:
    """Write one day of entity_baseline. Idempotent on the natural key."""
    rows = compute(con, day)
    n = upsert(con, "entity_baseline", rows,
               key=("as_of", "entity_type", "entity_id", "metric"))
    log.debug("baselines %s: %d rows", day, n)
    return n


def read_day(con, day: date, metric: str | None = None) -> list[dict]:
    """Read back what build_day wrote. Detectors work off this, not off raw SQL."""
    sql = "select " + ", ".join(COLUMNS) + " from entity_baseline where as_of = ?"
    params: list = [day]
    if metric:
        sql += " and metric = ?"
        params.append(metric)
    return [dict(zip(COLUMNS, r)) for r in con.execute(sql, params).fetchall()]
