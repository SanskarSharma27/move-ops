"""Weekday-aware rolling context. One row per (as_of, entity_type, entity_id, metric).

The single most important line in this service:

    the baseline is trailing 28 days, SAME WEEKDAY ONLY

Fleet on-time is 96% on Sundays and 60% on Tuesdays. A naive trailing mean therefore
reports a punctuality triumph every Sunday and a collapse every Tuesday, and six of
those false positives are sitting in this dataset already. Comparing Tuesdays to
Tuesdays deletes all of them without deleting a single real event.

Two further choices worth stating, because both change what fires:

*Centre and scale are estimated separately.* Twenty-eight days of one weekday is four
observations - enough to locate a Tuesday, nowhere near enough to measure how much a
Tuesday normally moves. `baseline_mean` is the median of those four same-weekday values.
`baseline_sd` takes the largest of four estimates, because a scale that is too small is
how a detector invents news:

  1. the sampling noise of today's own sample. A rate measured on 23 trips carries
     about eight points of standard error before anything has gone wrong. Rates use
     the Agresti-Coull adjusted proportion rather than the raw one, because a vendor
     whose baseline is a clean 100% has a textbook binomial error of exactly zero and
     would otherwise score minus infinity the first time one trip ran late. Counts use
     Poisson; everything else divides its own within-day dispersion by sqrt(n).
  2. that sampling noise multiplied by a pooled *overdispersion factor*. Each of the
     trailing 28 days is reduced to its residual against its own weekday's centre and
     divided by that day's own standard error; the robust median of those 28 ratios
     says how much noisier this entity runs than sampling alone would explain. This is
     the estimate that matters, because sample size is not constant across the week:
     a vendor runs 900 trips on Tuesday and 67 on Sunday, so a scale learned in units
     of points would be far too tight every Sunday. Learned as a multiple of standard
     error, it travels correctly.
  3. the same-weekday robust scale, 1.4826 x MAD over those four values, which catches
     a weekday that is genuinely more volatile than the rest of the week - Cedar Ridge
     Wednesdays swing seventeen points while its Sundays sit inside two.
  4. a per-metric floor, so a very stable series cannot manufacture a large z from a
     rounding wobble.

Both robust statistics use the median for the same reason: one prior outlier must not
drag the centre toward itself and inflate the scale, or the *next* occurrence of a
recurring problem scores as normal.

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
RESIDUAL_MIN_N = 8      # fewer residuals than this and the pooled scale is not worth trusting
PEER_MIN_N = 2          # vanta-Aus has two offices; a two-way comparison is still a comparison


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
           count(*) as n_trips,
           avg(seat_util) as seat_util, count(seat_util) as n_seat,
           stddev_samp(seat_util) as seat_util_sd
    from mis.trips where business_unit is not null group by 1, 2, 3, 4
  union all
    select trip_date, 'office', business_unit || ' / ' || office, business_unit,
           100.0 * avg(case when is_ontime_15 then 1.0 else 0.0 end), count(*),
           avg(seat_util), count(seat_util), stddev_samp(seat_util)
    from mis.trips where business_unit is not null and office is not null group by 1, 2, 3, 4
  union all
    select t.trip_date, 'vendor', t.vendor, p.parent_id,
           100.0 * avg(case when t.is_ontime_15 then 1.0 else 0.0 end), count(*),
           avg(t.seat_util), count(t.seat_util), stddev_samp(t.seat_util)
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
           stddev_samp(ack_minutes) as ack_sd,
           cast(count(*) filter (where severity = 'Sev-1') as double) as sev1_count,
           count(*) as n_alerts
    from mis.alerts where business_unit is not null and raised_at is not null
    group by 1, 2, 3, 4
  union all
    select cast(a.raised_at as date), 'office', t.business_unit || ' / ' || t.office,
           t.business_unit, avg(a.ack_minutes), count(a.ack_minutes),
           stddev_samp(a.ack_minutes),
           cast(count(*) filter (where a.severity = 'Sev-1') as double), count(*)
    from mis.alerts a join mis.trips t on t.trip_id = a.trip_id
    where a.raised_at is not null and t.office is not null group by 1, 2, 3, 4
  union all
    select cast(a.raised_at as date), 'vendor', t.vendor, p.parent_id,
           avg(a.ack_minutes), count(a.ack_minutes), stddev_samp(a.ack_minutes),
           cast(count(*) filter (where a.severity = 'Sev-1') as double), count(*)
    from mis.alerts a join mis.trips t on t.trip_id = a.trip_id
    left join {PARENTS} p on p.vendor = t.vendor
    where a.raised_at is not null and t.vendor is not null group by 1, 2, 3, 4
)
    select d, entity_type, entity_id, parent_id, 'ota15' as metric,
           ota15 as value, n_trips as n, cast(null as double) as value_sd
    from trip_grain where n_trips > 0
  union all
    select d, entity_type, entity_id, parent_id, 'seat_util', seat_util, n_seat, seat_util_sd
    from trip_grain where seat_util is not null
  union all
    select d, entity_type, entity_id, parent_id, 'noshow_pct', noshow_pct, n_legs, null
    from leg_grain where n_legs > 0
  union all
    select d, entity_type, entity_id, parent_id, 'ack_minutes', ack_minutes, n_acked, ack_sd
    from alert_grain where n_acked > 0
  union all
    select d, entity_type, entity_id, parent_id, 'sev1_count', sev1_count, n_alerts, null
    from alert_grain where n_alerts > 0
"""


def facts_for(con, day: date, metric: str) -> list[dict]:
    """The raw daily grid for one date and metric. Detectors read this."""
    ensure_facts(con)
    cur = con.execute(
        f"select d, entity_type, entity_id, parent_id, metric, value, n, value_sd "
        f"from {FACTS} where d = ? and metric = ?", [day, metric])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------- baselines

_SD_FLOOR_CASE = "case t.metric " + " ".join(
    f"when '{m}' then {cfg[1]}" for m, cfg in METRICS.items()) + " else 1.0 end"

def _sampling_se(metric: str, centre: str, n: str, value_sd: str) -> str:
    """SQL for the standard error one day's own sample carries.

    Rates use the Agresti-Coull adjusted proportion, (k + 2) / (n + 4), not the raw one.
    The textbook binomial error at p = 1 is exactly zero, so an entity with a spotless
    six-trip baseline would score an infinite z the first time one trip ran late. The
    two-successes-two-failures adjustment pulls p off the boundary and the problem goes
    away; at n = 971 it moves the fourth decimal place and nothing else.
    """
    p = (f"least(greatest((least(greatest(coalesce({centre}, 50.0), 0.0), 100.0) / 100.0"
         f" * {n} + 2) / ({n} + 4), 0.001), 0.999)")
    return f"""
    case {metric}
         when 'ota15'      then 100.0 * sqrt({p} * (1 - {p}) / greatest({n}, 1))
         when 'noshow_pct' then 100.0 * sqrt({p} * (1 - {p}) / greatest({n}, 1))
         when 'sev1_count' then sqrt(greatest(coalesce({centre}, 1.0), 1.0))
         else coalesce({value_sd} / sqrt(greatest({n}, 1)), 0.0)
    end"""

# peer_pctile always reads "how good", 0 = worst in the peer group, so it is oriented by
# the metric's direction: high on-time is good, high acknowledgement minutes is not.
_GOOD_DIRECTION = "case t.metric " + " ".join(
    f"when '{m}' then {'1' if cfg[2] else '-1'}" for m, cfg in METRICS.items()) + " else 1 end"

_HIST_SE = _sampling_se("h.metric", "w.med", "h.n", "h.value_sd")
_TODAY_SE = _sampling_se("t.metric", "c.med", "t.n", "t.value_sd")

_BASELINE_SQL = f"""
with today as (
    select * from {FACTS} where d = $as_of
),
hist as (
    select *, dayofweek(d) as dow from {FACTS}
    where d >= $as_of - interval '28 days' and d < $as_of
),
-- the centre of each weekday inside the window: this is what carries the 96%-Sunday,
-- 60%-Tuesday shape that makes a naive trailing mean useless.
weekday_centre as (
    select entity_type, entity_id, metric, dow, median(value) as med, count(*) as k
    from hist group by 1, 2, 3, 4
),
weekday_scale as (
    select h.entity_type, h.entity_id, h.metric,
           1.4826 * median(abs(h.value - w.med)) as wmad
    from hist h join weekday_centre w
      on w.entity_type = h.entity_type and w.entity_id = h.entity_id
     and w.metric = h.metric and w.dow = h.dow
    where h.dow = dayofweek($as_of)
    group by 1, 2, 3
),
-- every day in the window reduced to its residual against its own weekday's centre,
-- and then to a multiple of that day's own standard error. Working in units of
-- standard error is what makes the estimate portable across a week whose sample
-- sizes differ by a factor of ten.
residual as (
    select h.entity_type, h.entity_id, h.metric, h.value - w.med as r,
           {_HIST_SE} as se_h
    from hist h join weekday_centre w
      on w.entity_type = h.entity_type and w.entity_id = h.entity_id
     and w.metric = h.metric and w.dow = h.dow
),
scale as (
    select entity_type, entity_id, metric,
           median(abs(r)) as mad, stddev_samp(r) as sd, count(*) as rn,
           greatest(1.0, 1.4826 * median(abs(r) / se_h) filter (where se_h > 0)) as phi,
           count(*) filter (where se_h > 0) as phi_n
    from residual group by 1, 2, 3
),
centre as (
    select entity_type, entity_id, metric, med, k as bn
    from weekday_centre where dow = dayofweek($as_of)
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
           case when s.rn >= {RESIDUAL_MIN_N} then greatest(
                    {_TODAY_SE},                                    -- today's own sample
                    case when s.phi_n >= {RESIDUAL_MIN_N}
                         then s.phi * ({_TODAY_SE}) else 0 end,     -- x overdispersion
                    coalesce(nullif(1.4826 * s.mad, 0), s.sd, 0),   -- pooled, in units
                    coalesce(ws.wmad, 0),                           -- this weekday only
                    {_SD_FLOOR_CASE}) end as baseline_sd,
           r.slope_28d,
           {_GOOD_DIRECTION} as good_dir,
           case t.entity_type
                when 'business_unit' then 'business units'
                when 'office' then coalesce(t.parent_id, '?') || ' offices'
                when 'vendor' then coalesce(t.parent_id, '?') || ' vendors'
           end as peer_group
    from today t
    left join centre c
      on c.entity_type = t.entity_type and c.entity_id = t.entity_id and c.metric = t.metric
    left join scale s
      on s.entity_type = t.entity_type and s.entity_id = t.entity_id and s.metric = t.metric
    left join weekday_scale ws
      on ws.entity_type = t.entity_type and ws.entity_id = t.entity_id and ws.metric = t.metric
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
                                              order by j.good_dir * j.value) end as peer_pctile,
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
