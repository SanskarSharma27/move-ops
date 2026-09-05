"""Observation and verification.

`observe` is the only place this service reads a metric out of the raw data, so
cases, predictions and auto-resolution all agree on what a number means.

Verification semantics: a prediction covers the window (made_on, verify_on]. It is
`confirmed` if any qualifying day in that window satisfies the predicate, `refuted`
if qualifying days exist and none does, and `unverifiable` if the entity had no
qualifying days at all. Guessing is not an option — `unverifiable` is a real outcome.
"""
from __future__ import annotations

import datetime as dt

from common import make_id, upsert

HORIZON_DAYS = 7

# metric -> (from-clause, date expression, value expression, n expression, min n, unit, label)
# Everything joins through mis.trips where the source table lacks the entity columns,
# so one entity filter works for every metric.
METRICS: dict[str, dict] = {
    "ota15": dict(
        frm="mis.trips t", date="t.trip_date",
        value="100.0 * count(*) filter (where t.is_ontime_15) / count(*)",
        n="count(*)", min_n=40, unit="%", label="on-time arrival"),
    "seat_util": dict(
        frm="mis.trips t", date="t.trip_date",
        value="avg(t.seat_util)", n="count(t.seat_util)",
        min_n=40, unit="", label="seat utilisation"),
    "noshow_pct": dict(
        frm="mis.emp_legs t", date="t.trip_date",
        value="100.0 * avg(case when t.is_no_show then 1.0 else 0.0 end)",
        n="count(*)", min_n=40, unit="%", label="no-show rate"),
    "ack_minutes": dict(
        frm="mis.alerts a left join mis.trips t on t.trip_id = a.trip_id",
        date="a.raised_at::date", value="avg(a.ack_minutes)",
        n="count(a.ack_minutes)", min_n=5, unit=" minutes",
        label="alert acknowledgement time"),
    "sev1_count": dict(
        frm="mis.alerts a left join mis.trips t on t.trip_id = a.trip_id",
        date="a.raised_at::date", value="count(*) filter (where a.severity = 'Sev-1')",
        n="count(*)", min_n=1, unit=" alerts", label="Sev-1 alert count"),
    "cost_per_trip": dict(
        frm="mis.bills b join mis.trips t on t.trip_id = b.trip_id", date="t.trip_date",
        value="sum(b.trip_cost) / count(distinct b.trip_id)", n="count(*)",
        min_n=40, unit="", label="cost per trip"),
}

# Metrics where a higher number is the problem. Drives the predicate we predict with.
WORSE_WHEN_HIGH = {"ack_minutes", "noshow_pct", "sev1_count", "cost_per_trip"}


def _entity_filter(metric: str, entity_type: str, entity_id: str):
    """SQL fragment + params restricting a metric's base tables to one entity.

    Returns (None, None) when the grain is not expressible on that base — a vendor
    has no column on mis.emp_legs — so the caller records `unverifiable` rather
    than inventing a number.
    """
    frm = METRICS[metric]["frm"]
    bu = "coalesce(t.business_unit, a.business_unit)" if " mis.alerts a" in frm else "t.business_unit"
    if entity_type == "business_unit":
        return f"{bu} = ?", [entity_id]
    if entity_type == "office":
        # office entity_id is ALWAYS "business_unit / Office" — the same site name
        # exists under two business units with a 14-point punctuality gap.
        unit, _, office = entity_id.partition(" / ")
        return f"{bu} = ? and t.office = ?", [unit, office or entity_id]
    if entity_type == "vendor":
        if "mis.emp_legs" in frm:
            return None, None
        return ("b.vendor = ?", [entity_id]) if "mis.bills b" in frm else ("t.vendor = ?", [entity_id])
    if entity_type == "shift":
        return "t.shift_type = ?", [entity_id]
    if entity_type == "contract":
        return ("b.contract = ?", [entity_id]) if "mis.bills b" in frm else (None, None)
    return None, None


def observe(con, metric: str, entity_type: str, entity_id: str,
            start: dt.date, end: dt.date) -> list[tuple[dt.date, float, int]]:
    """Daily (date, value, n) for one entity and metric, inclusive of both ends.

    Days below the metric's minimum sample are dropped: 58 of 84 raw July signals
    have n < 40 at a mean |z| of 2.56, so an unfiltered series verifies against noise.
    """
    spec = METRICS.get(metric)
    if spec is None or start > end:
        return []
    where, params = _entity_filter(metric, entity_type, entity_id)
    if where is None:
        return []
    rows = con.execute(
        f"""select {spec['date']} as d, {spec['value']} as v, {spec['n']} as n
            from {spec['frm']}
            where {spec['date']} between ? and ? and {where}
            group by 1 having {spec['n']} >= {spec['min_n']} order by 1""",
        [start, end, *params],
    ).fetchall()
    return [(d, float(v), int(n)) for d, v, n in rows if v is not None]


def holds(predicate: str, value: float, threshold: float, threshold_hi: float | None) -> bool:
    if predicate == "lt":
        return value < threshold
    if predicate == "gt":
        return value > threshold
    if predicate == "between":
        return threshold <= value <= (threshold_hi if threshold_hi is not None else threshold)
    raise ValueError(f"unknown predicate {predicate!r}")


def trailing(con, metric, entity_type, entity_id, day, days: int = 28) -> float | None:
    """Median of the entity's own recent daily values — what 'normal' means for it."""
    series = observe(con, metric, entity_type, entity_id,
                     day - dt.timedelta(days=days), day)
    if not series:
        return None
    vals = sorted(v for _, v, _ in series)
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def make_prediction(con, case: dict, metric: str, day: dt.date) -> dict | None:
    """A falsifiable claim with a date, pitched against the entity's own recent norm.

    A threshold everyone always clears is not a prediction; the entity's trailing
    median keeps it honest for sites that are already bad.
    """
    if metric not in METRICS:
        return None
    base = trailing(con, metric, case["entity_type"], case["entity_id"], day)
    if base is None:
        return None
    unit = METRICS[metric]["unit"]
    label = METRICS[metric]["label"]
    if metric in WORSE_WHEN_HIGH:
        predicate, threshold = "gt", round(max(base * 1.5, base + 1.0), 1)
    elif case["detector"] == "metric_integrity":
        # The Santa Clara case: the level is real, the improvement is not. Predict the
        # inflated level persists while the underlying journey does not change.
        predicate, threshold = "gt", round(base * 0.85, 1)
    else:
        predicate, threshold = "lt", round(min(80.0, base - 5.0), 1)
    verify_on = day + dt.timedelta(days=HORIZON_DAYS)
    direction = "above" if predicate == "gt" else "below"
    statement = (f"{case['entity_id']} {label} will go {direction} {threshold}{unit} "
                 f"again on at least one day within seven days.")
    return {
        "prediction_id": make_id(case["case_id"], day, metric),
        "case_id": case["case_id"], "made_on": day, "verify_on": verify_on,
        "statement": statement, "metric": metric,
        "entity_type": case["entity_type"], "entity_id": case["entity_id"],
        "predicate": predicate, "threshold": float(threshold), "threshold_hi": None,
        "outcome": None, "observed": None, "verified_on": None,
    }


def ensure_pending(con, day: dt.date, cases: list[dict]) -> int:
    """Every case that is still a problem carries exactly one open prediction."""
    rows = []
    for case in cases:
        if case["status"] == "resolved" or not case.get("metric"):
            continue
        pending = con.execute(
            "select count(*) from prediction where case_id = ? and outcome is null",
            [case["case_id"]],
        ).fetchone()[0]
        if pending:
            continue
        row = make_prediction(con, case, case["metric"], day)
        if row:
            rows.append(row)
    return upsert(con, "prediction", rows, key="prediction_id")


def verify_due(con, day: dt.date) -> list[dict]:
    """Score every prediction whose verify_on has arrived. Refutations are kept.

    An agent that shows a wrong prediction is more credible than one that shows
    only wins, so nothing here is quietly dropped.
    """
    due = con.execute(
        """select prediction_id, made_on, verify_on, metric, entity_type, entity_id,
                  predicate, threshold, threshold_hi
           from prediction where outcome is null and verify_on <= ?""",
        [day],
    ).fetchall()
    out = []
    for pid, made_on, verify_on, metric, etype, eid, predicate, threshold, thi in due:
        window_end = min(verify_on, day)
        series = observe(con, metric, etype, eid,
                         made_on + dt.timedelta(days=1), window_end)
        hits = [(d, v) for d, v, _ in series if holds(predicate, v, threshold, thi)]
        if hits:
            outcome = "confirmed"
            verified_on, observed = hits[0][0], (
                min(v for _, v in hits) if predicate == "lt" else max(v for _, v in hits))
        elif series:
            outcome = "refuted"
            # The nearest miss is the honest number to show next to a refutation.
            verified_on, observed = window_end, (
                min(v for _, v, _ in series) if predicate == "lt"
                else max(v for _, v, _ in series))
        else:
            outcome, verified_on, observed = "unverifiable", window_end, None
        con.execute(
            "update prediction set outcome = ?, observed = ?, verified_on = ? where prediction_id = ?",
            [outcome, observed, verified_on, pid])
        out.append({"prediction_id": pid, "outcome": outcome, "observed": observed})
    return out
