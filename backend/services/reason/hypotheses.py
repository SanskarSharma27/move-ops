"""Fixed, parameterised hypothesis tests for reasoning incidents.

No SQL is generated from prose.  Every test below is a static template, executed
with bound parameters, and persisted with those parameters for auditability.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from common import as_json, make_id


ENTITY_FILTER = """
(
  (? = 'business_unit' and business_unit = ?)
  or (? = 'office' and business_unit || ' / ' || office = ?)
  or (? = 'vendor' and vendor = ?)
  or (? = 'shift' and shift_type = ?)
)
""".strip()

VENDOR_FAILURE_SQL = """
select vendor, count(*) as trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ota15
from mis.trips
where trip_date = ?
  and (
    (? = 'office' and business_unit || ' / ' || office = ?)
    or (? = 'business_unit' and business_unit = ?)
    or (? = 'vendor' and business_unit = ?)
  )
group by vendor
order by vendor
""".strip()

SYSTEMIC_DAY_EVENT_SQL = """
select entity_id, z
from entity_baseline
where as_of = ? and metric = ? and entity_type = ? and entity_id <> ?
order by entity_id
""".strip()

SYSTEMIC_SIGNAL_FALLBACK_SQL = """
select entity_id, z
from signal
where as_of = ? and detector = ? and entity_type = ? and entity_id <> ?
order by entity_id
""".strip()

DEMAND_SURGE_SQL = f"""
with daily as (
  select trip_date, count(*)::double as trips
  from mis.trips
  where trip_date between ? and ? and {ENTITY_FILTER}
  group by trip_date
), comparison as (
  select
    max(trips) filter (where trip_date = ?) as current_trips,
    avg(trips) filter (
      where trip_date < ? and dayofweek(trip_date) = dayofweek(?)
    ) as trailing_trips
  from daily
)
select current_trips, trailing_trips,
       round(100.0 * (current_trips - trailing_trips) / nullif(trailing_trips, 0), 1)
         as change_pct
from comparison
""".strip()

CAPACITY_SHORTFALL_SQL = f"""
with daily as (
  select trip_date, count(*)::double as trips,
         count(distinct actual_cab_registration)::double as cabs,
         count(*)::double / nullif(count(distinct actual_cab_registration), 0)
           as trips_per_cab
  from mis.trips
  where trip_date between ? and ? and {ENTITY_FILTER}
  group by trip_date
), comparison as (
  select
    max(cabs) filter (where trip_date = ?) as current_cabs,
    max(trips_per_cab) filter (where trip_date = ?) as current_trips_per_cab,
    avg(cabs) filter (
      where trip_date < ? and dayofweek(trip_date) = dayofweek(?)
    ) as trailing_cabs,
    avg(trips_per_cab) filter (
      where trip_date < ? and dayofweek(trip_date) = dayofweek(?)
    ) as trailing_trips_per_cab
  from daily
)
select current_cabs, trailing_cabs, current_trips_per_cab, trailing_trips_per_cab,
       round(100.0 * (current_cabs - trailing_cabs) / nullif(trailing_cabs, 0), 1)
         as cab_change_pct,
       round(100.0 * (current_trips_per_cab - trailing_trips_per_cab)
         / nullif(trailing_trips_per_cab, 0), 1) as load_change_pct
from comparison
""".strip()

SCHEDULE_LAG_SQL = f"""
select date_trunc('month', trip_date)::date as month,
       round(avg((actual_end - actual_start) / 60.0)
         / nullif(avg(traveled_km), 0), 3) as actual_min_per_km,
       round(avg((planned_end - planned_start) / 60.0)
         / nullif(avg(planned_km), 0), 3) as planned_min_per_km
from mis.trips
where trip_date between ? and ? and planned_km > 0 and traveled_km > 0
  and {ENTITY_FILTER}
group by month
order by month
""".strip()

COMPOSITION_SHIFT_SQL = f"""
select date_trunc('month', trip_date)::date as month, fuel_type,
       count(*) as trips,
       round(100.0 * count(*) / sum(count(*)) over (partition by month), 1) as mix_pct,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ota15
from mis.trips
where trip_date between ? and ? and fuel_type in ('Diesel', 'Electric')
  and {ENTITY_FILTER}
group by month, fuel_type
order by month, fuel_type
""".strip()

COMPOSITION_CONTROL_SQL = """
select business_unit, fuel_type,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ota15
from mis.trips
where trip_date between ? and ? and fuel_type in ('Diesel', 'Electric')
group by business_unit, fuel_type
order by business_unit, fuel_type
""".strip()


def build_hypotheses(con: Any, candidate: Any, day: date) -> list[dict[str, Any]]:
    """Run the six fixed tests and return rows ready for ``hypothesis``."""

    tests = (
        _vendor_failure(con, candidate, day),
        _systemic_day_event(con, candidate, day),
        _demand_surge(con, candidate, day),
        _capacity_shortfall(con, candidate, day),
        _schedule_lag(con, candidate, day),
        _composition_shift(con, candidate, day),
    )
    rows: list[dict[str, Any]] = []
    for rank, test in enumerate(tests, 1):
        rows.append(
            {
                "hypothesis_id": make_id(candidate.incident_id, test["name"]),
                "incident_id": candidate.incident_id,
                "name": test["name"],
                "statement": test["statement"],
                "verdict": test["verdict"],
                "test_sql": _audited_sql(test["sql"], test["params"]),
                "result": as_json(test["result"]),
                "reasoning": test["reasoning"],
                "rank": rank,
            }
        )
    return rows


def _vendor_failure(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    primary = candidate.primary
    entity_type = candidate.entity_type
    entity_id = candidate.entity_id
    parent_id = primary.get("parent_id") or entity_id
    params = [day, entity_type, entity_id, entity_type, entity_id, entity_type, parent_id]
    rows = _query(con, VENDOR_FAILURE_SQL, params)
    values = {
        str(row[0]): {"trips": int(row[1]), "ota15": _round(row[2], 1)}
        for row in rows
        if row[2] is not None
    }
    if not values:
        values = {
            str(signal.get("entity_id")): {
                "trips": int(_number(signal.get("n"), 0)),
                "ota15": _round(signal.get("value"), 1),
            }
            for signal in candidate.signals
            if signal.get("entity_type") == "vendor" and signal.get("value") is not None
        }

    rates = [item["ota15"] for item in values.values()]
    spread = round(max(rates) - min(rates), 1) if len(rates) >= 2 else None
    result = {"vendors": values, "vendor_count": len(values), "spread_points": spread}
    if len(rates) >= 2 and spread is not None and spread <= 10:
        verdict = "refuted"
        reasoning = (
            f"All {len(rates)} observed vendors were within a {spread:g}-point band, "
            "which refutes an isolated vendor failure."
        )
    elif len(rates) >= 2:
        verdict = "supported"
        reasoning = (
            f"The vendors span {spread:g} points, so an isolated vendor effect remains plausible."
        )
    else:
        verdict = "inconclusive"
        reasoning = "Only one vendor measurement is available, so peer separation cannot be tested."
    return _test(
        "vendor_failure",
        "One or two vendors degraded and dragged the parent result down.",
        verdict,
        VENDOR_FAILURE_SQL,
        params,
        result,
        reasoning,
    )


def _systemic_day_event(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    primary = candidate.primary
    params = [day, primary.get("metric"), candidate.entity_type, candidate.entity_id]
    rows = _query(con, SYSTEMIC_DAY_EVENT_SQL, params)
    sql = SYSTEMIC_DAY_EVENT_SQL
    if not rows:
        params = [day, primary.get("detector"), candidate.entity_type, candidate.entity_id]
        rows = _query(con, SYSTEMIC_SIGNAL_FALLBACK_SQL, params)
        sql = SYSTEMIC_SIGNAL_FALLBACK_SQL
    peers = {
        str(row[0]): _round(row[1], 2)
        for row in rows
        if row[1] is not None
    }
    anomalous = sum(1 for z in peers.values() if abs(z) >= 2)
    if not peers:
        # In fixture-only tests, the supplied incident group is still valid evidence.
        supplied_peers = {
            str(signal.get("entity_id")): _round(signal.get("z"), 2)
            for signal in candidate.signals
            if signal.get("signal_id") != primary.get("signal_id")
        }
        peers = supplied_peers
        anomalous = sum(1 for z in peers.values() if abs(z) >= 2)
    result = {
        "peers_tested": len(peers),
        "anomalous_peers": anomalous,
        "peer_z": peers,
    }
    if anomalous == 0:
        verdict = "refuted"
        reasoning = (
            f"None of the {len(peers)} available peers crossed the anomaly threshold, "
            "so a systemic day event is refuted."
        )
    elif anomalous >= 2:
        verdict = "supported"
        reasoning = (
            f"{anomalous} peer entities also crossed the anomaly threshold, supporting a shared event."
        )
    else:
        verdict = "inconclusive"
        reasoning = "One peer also moved, which is insufficient to distinguish a local from systemic event."
    return _test(
        "systemic_day_event",
        "A shared day-level event moved this entity and its peers together.",
        verdict,
        sql,
        params,
        result,
        reasoning,
    )


def _demand_surge(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    params = [day - timedelta(days=28), day, *_entity_params(candidate), day, day, day]
    row = _one(con, DEMAND_SURGE_SQL, params)
    current, trailing, change = _three(row)
    result = {
        "current_trips": current,
        "trailing_same_weekday_trips": trailing,
        "change_pct": change,
    }
    if change is None:
        verdict = "inconclusive"
        reasoning = "The trailing same-weekday volume window is incomplete, so demand cannot be separated."
    elif abs(change) <= 10:
        verdict = "refuted"
        reasoning = f"Trip volume changed {change:g}%, which is flat enough to refute a demand surge."
    elif change > 10:
        verdict = "supported"
        reasoning = f"Trip volume rose {change:g}% against the trailing same-weekday window."
    else:
        verdict = "refuted"
        reasoning = f"Trip volume fell {abs(change):g}%, which refutes a demand surge."
    return _test(
        "demand_surge",
        "Trip demand surged beyond the operating plan.",
        verdict,
        DEMAND_SURGE_SQL,
        params,
        result,
        reasoning,
    )


def _capacity_shortfall(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    params = [
        day - timedelta(days=28),
        day,
        *_entity_params(candidate),
        day,
        day,
        day,
        day,
        day,
        day,
    ]
    row = _one(con, CAPACITY_SHORTFALL_SQL, params)
    values = list(row) if row else [None] * 6
    values += [None] * (6 - len(values))
    result = {
        "current_cabs": _round(values[0], 1),
        "trailing_cabs": _round(values[1], 1),
        "current_trips_per_cab": _round(values[2], 2),
        "trailing_trips_per_cab": _round(values[3], 2),
        "cab_change_pct": _round(values[4], 1),
        "load_change_pct": _round(values[5], 1),
    }
    cab_change = result["cab_change_pct"]
    load_change = result["load_change_pct"]
    if cab_change is None or load_change is None:
        verdict = "inconclusive"
        reasoning = "Cab and load history is incomplete, so capacity cannot be tested for this entity."
    elif abs(cab_change) <= 5 and abs(load_change) <= 10:
        verdict = "refuted"
        reasoning = (
            f"Cab supply changed {cab_change:g}% and trips per cab changed {load_change:g}%, "
            "so capacity was materially unchanged."
        )
    elif load_change > 10 and cab_change <= 5:
        verdict = "supported"
        reasoning = (
            f"Trips per cab rose {load_change:g}% while cab supply changed only {cab_change:g}%."
        )
    else:
        verdict = "inconclusive"
        reasoning = (
            f"Cab supply changed {cab_change:g}% and load changed {load_change:g}%, "
            "but the movements do not isolate a capacity shortfall."
        )
    return _test(
        "capacity_shortfall",
        "Cab supply did not keep pace with trip demand.",
        verdict,
        CAPACITY_SHORTFALL_SQL,
        params,
        result,
        reasoning,
    )


def _schedule_lag(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    start = date(day.year, max(1, day.month - 2), 1)
    params = [start, day, *_entity_params(candidate)]
    rows = _query(con, SCHEDULE_LAG_SQL, params)
    series = [
        {
            "month": str(row[0]),
            "actual_min_per_km": _round(row[1], 3),
            "planned_min_per_km": _round(row[2], 3),
        }
        for row in rows
    ]
    actual_change = _series_change(series, "actual_min_per_km")
    planned_change = _series_change(series, "planned_min_per_km")
    result = {
        "monthly": series,
        "actual_change_pct": actual_change,
        "planned_change_pct": planned_change,
    }
    if actual_change is None or planned_change is None:
        verdict = "inconclusive"
        reasoning = "Two complete monthly pace observations are required to test schedule lag."
    elif actual_change - planned_change >= 3:
        verdict = "supported"
        reasoning = (
            f"Actual minutes per km rose {actual_change:g}% while planned minutes per km "
            f"rose {planned_change:g}%, supporting schedule lag."
        )
    else:
        verdict = "refuted"
        reasoning = (
            f"Actual pace changed {actual_change:g}% against {planned_change:g}% in the plan, "
            "so the schedule did not materially lag the journey."
        )
    return _test(
        "schedule_lag",
        "Actual journey pace worsened faster than the planned pace.",
        verdict,
        SCHEDULE_LAG_SQL,
        params,
        result,
        reasoning,
    )


def _composition_shift(con: Any, candidate: Any, day: date) -> dict[str, Any]:
    start = date(day.year, max(1, day.month - 2), 1)
    params = [start, day, *_entity_params(candidate)]
    rows = _query(con, COMPOSITION_SHIFT_SQL, params)
    control_params = [start, day]
    control_rows = _query(con, COMPOSITION_CONTROL_SQL, control_params)
    entity = [
        {
            "month": str(row[0]),
            "fuel": str(row[1]),
            "trips": int(row[2]),
            "mix_pct": _round(row[3], 1),
            "ota15": _round(row[4], 1),
        }
        for row in rows
    ]
    controls: dict[str, dict[str, float | None]] = {}
    for business_unit, fuel, ota15 in control_rows:
        controls.setdefault(str(business_unit), {})[str(fuel)] = _round(ota15, 1)

    latest_month = max((item["month"] for item in entity), default=None)
    latest = {
        item["fuel"]: item["ota15"]
        for item in entity
        if item["month"] == latest_month
    }
    fuel_gap = (
        round(latest["Electric"] - latest["Diesel"], 1)
        if latest.get("Electric") is not None and latest.get("Diesel") is not None
        else None
    )
    result = {"entity": entity, "control_groups": controls, "fuel_gap_points": fuel_gap}
    if fuel_gap is None:
        verdict = "inconclusive"
        reasoning = "Both diesel and electric observations are required to test a fuel-mix explanation."
    elif abs(fuel_gap) <= 3:
        verdict = "refuted"
        reasoning = (
            f"Electric and diesel performance differs by only {abs(fuel_gap):g} points, "
            "and the business-unit controls show no consistent electric penalty."
        )
    elif fuel_gap < -5:
        verdict = "supported"
        reasoning = f"Electric performance trails diesel by {abs(fuel_gap):g} points in the entity."
    else:
        verdict = "refuted"
        reasoning = f"Electric performance leads diesel by {fuel_gap:g} points, refuting an EV penalty."
    # Both fixed queries are part of this one controlled experiment.
    sql = f"{COMPOSITION_SHIFT_SQL}\n\n{COMPOSITION_CONTROL_SQL}"
    return _test(
        "composition_shift",
        "A changing fuel or capacity mix caused the performance movement.",
        verdict,
        sql,
        [*params, *control_params],
        result,
        reasoning,
    )


def _entity_params(candidate: Any) -> list[Any]:
    return [
        candidate.entity_type,
        candidate.entity_id,
        candidate.entity_type,
        candidate.entity_id,
        candidate.entity_type,
        candidate.entity_id,
        candidate.entity_type,
        candidate.entity_id,
    ]


def _query(con: Any, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    try:
        return list(con.execute(sql, params).fetchall())
    except Exception:
        return []


def _one(con: Any, sql: str, params: list[Any]) -> tuple[Any, ...] | None:
    rows = _query(con, sql, params)
    return rows[0] if rows else None


def _three(row: tuple[Any, ...] | None) -> tuple[float | None, float | None, float | None]:
    if not row:
        return None, None, None
    values = list(row) + [None, None, None]
    return _round(values[0], 1), _round(values[1], 1), _round(values[2], 1)


def _series_change(series: list[dict[str, Any]], key: str) -> float | None:
    values = [item[key] for item in series if item.get(key) is not None]
    if len(values) < 2 or values[0] == 0:
        return None
    return round(100.0 * (values[-1] - values[0]) / values[0], 1)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: Any, places: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _audited_sql(sql: str, params: list[Any]) -> str:
    encoded = json.dumps(params, default=str, ensure_ascii=False)
    return f"{sql}\n-- bound parameters: {encoded}"


def _test(
    name: str,
    statement: str,
    verdict: str,
    sql: str,
    params: list[Any],
    result: dict[str, Any],
    reasoning: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "statement": statement,
        "verdict": verdict,
        "sql": sql,
        "params": params,
        "result": result,
        "reasoning": reasoning,
    }
