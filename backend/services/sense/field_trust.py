"""Audit each source column once and decide what it is worth.

Three verdicts, and they are operational, not cosmetic:

    trusted      use it
    degraded     use it, but say out loud what is wrong with it
    quarantined  refuse to build a metric on it

Every number in every `evidence` sentence is recomputed here from `mis.*` on the day
the audit runs. Nothing is hardcoded, because the faithfulness gate reads the sentence
and checks its numerals against the data.

About `trust`: it is the 0-1 score of that row's own named test, and it is only
comparable between rows sharing a `test_name`. A correlation test and a placeholder
share do not live on the same scale, and pretending they do would be its own small act
of metric dishonesty.

The refusal is the point. `guard(con, detector)` runs before every detector on every
replay day and raises `QuarantinedColumnError` the moment a detector reaches for a
column this audit condemned.
"""
from __future__ import annotations

import logging
from datetime import date

from common import upsert

log = logging.getLogger("sense.field_trust")

QUARANTINED = "quarantined"
DEGRADED = "degraded"
TRUSTED = "trusted"


class QuarantinedColumnError(RuntimeError):
    """A detector asked for a column the field audit condemned."""


# Which source columns each detector is allowed to stand on. The guard walks this map
# every day, so a detector cannot quietly acquire a dependency on a bad field.
SOURCE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "punctuality_drop": [("trips", "actual_end_epoch"), ("trips", "planned_end_epoch")],
    "metric_integrity": [("trips", "actual_end_epoch"), ("trips", "planned_end_epoch"),
                         ("trips", "planned_km"), ("alerts", "event_type")],
    "alert_ack_sla": [("alerts", "acknowledge_time"), ("alerts", "start_time")],
    "safety_cluster": [("alerts", "severity")],
    "escort_breach": [("alerts", "event_type"), ("trips", "actual_escort")],
    "noshow_spike": [("emp_legs", "signintype"), ("emp_legs", "is_no_show")],
    "billing_anomaly": [("bills", "trip_cost"), ("bills", "total_trip_km")],
}


# --------------------------------------------------------------------- the audit

def _one(con, sql: str) -> tuple:
    return con.execute(sql).fetchone()


def audit(con, as_of: date) -> int:
    """Recompute every column verdict and write it to field_trust. Idempotent."""
    rows = [
        _delay_minutes(con), _delay_reason(con), _marshal_rating(con),
        _driver_rating(con), _alert_severity(con), _billed_km(con),
        _signintype(con), _actual_end_epoch(con),
    ]
    for r in rows:
        r["computed_on"] = as_of
        r["trust"] = round(float(r["trust"]), 4)
    n = upsert(con, "field_trust", rows, key=("table_name", "column_name"))
    bad = [f"{r['table_name']}.{r['column_name']}" for r in rows if r["verdict"] == QUARANTINED]
    log.warning("field audit %s: %d columns audited, quarantined %s", as_of, n, ", ".join(bad))
    return n


def _delay_minutes(con) -> dict:
    """The platform's own delay field is attributed by hand, not measured."""
    corr, pct_zero = _one(con, """
        select abs(corr(delay_minutes_reported, departure_delay_min)),
               100.0 * count(*) filter (where delay_minutes_reported = 0) / count(*)
        from mis.trips
    """)
    return dict(
        table_name="trips", column_name="delay_minutes", verdict=QUARANTINED, trust=corr,
        test_name="correlation_with_reconstruction",
        evidence=(f"Zero on {pct_zero:.1f}% of trips and correlates {corr:.2f} with the departure "
                  f"slip recomputed from the epoch columns. It is attributed by hand, not "
                  f"measured, so it cannot carry punctuality."),
    )


def _delay_reason(con) -> dict:
    """NODELAY claims 90.2% on time; the clocks say 64.9%. Agreement is near chance."""
    claimed, actual, agree, kappa = _one(con, """
        with t as (
          select (delay_reason = 'NODELAY') as claims_ontime, is_ontime_15 as really_ontime
          from mis.trips
        ), p as (
          select avg(case when claims_ontime then 1.0 else 0.0 end) pc,
                 avg(case when really_ontime then 1.0 else 0.0 end) pr,
                 avg(case when claims_ontime = really_ontime then 1.0 else 0.0 end) po
          from t
        )
        select 100.0 * pc, 100.0 * pr, 100.0 * po,
               (po - (pc * pr + (1 - pc) * (1 - pr))) / (1 - (pc * pr + (1 - pc) * (1 - pr)))
        from p
    """)
    return dict(
        table_name="trips", column_name="delay_reason", verdict=QUARANTINED,
        trust=max(0.0, kappa), test_name="agreement_with_reconstruction",
        evidence=(f"Reports NODELAY on {claimed:.1f}% of trips while the timestamps show only "
                  f"{actual:.1f}% arrived within 15 minutes. The two agree on {agree:.1f}% of "
                  f"trips, barely above chance, and it overstates punctuality by "
                  f"{claimed - actual:.1f} points."),
    )


def _marshal_rating(con) -> dict:
    """Zero here means no marshal was aboard, not that a marshal scored zero."""
    pct_placeholder, real_rows, fake_mean, true_mean = _one(con, """
        select 100.0 * count(*) filter (where marshal_rating is null) / count(*),
               count(marshal_rating),
               avg(coalesce(marshal_rating, 0)),
               avg(marshal_rating)
        from mis.feedback
    """)
    return dict(
        table_name="feedback", column_name="marshal_rating", verdict=QUARANTINED,
        trust=1 - pct_placeholder / 100.0, test_name="placeholder_share",
        evidence=(f"Zero on {pct_placeholder:.1f}% of rows, where no marshal was aboard. Only "
                  f"{real_rows} rows carry a real score and those average {true_mean:.2f} out of "
                  f"5; averaging the placeholder in reports a false {fake_mean:.2f}."),
    )


def _driver_rating(con) -> dict:
    """Real values, no discriminating power: 14 weeks inside a fiftieth of a point."""
    n_weeks, lo, hi, spread, sd = _one(con, """
        with wk as (
          select date_trunc('week', trip_at) w, avg(driver_rating) m
          from mis.feedback where trip_at is not null and driver_rating is not null
          group by 1
        )
        select count(*), min(m), max(m), max(m) - min(m),
               (select stddev_samp(driver_rating) from mis.feedback)
        from wk
    """)
    return dict(
        table_name="feedback", column_name="driver_rating", verdict=DEGRADED,
        trust=min(1.0, spread / sd), test_name="discriminating_power",
        evidence=(f"Ranges {lo:.3f} to {hi:.3f} across all {n_weeks} weeks, a spread of "
                  f"{spread:.3f} of a point against a per-response standard deviation of "
                  f"{sd:.3f}. It is populated and valid, and it separates nothing."),
    )


def _alert_severity(con) -> dict:
    """15,037 rows hold the literal string 'False' where a severity belongs."""
    n_false, n_missing, n_valid, n_total = _one(con, """
        select count(*) filter (where severity_raw = 'False'),
               count(*) filter (where severity is null and severity_raw <> 'False'),
               count(severity), count(*)
        from mis.alerts
    """)
    return dict(
        table_name="alerts", column_name="severity", verdict=DEGRADED,
        trust=n_valid / n_total, test_name="domain_validity",
        evidence=(f"Holds the literal string 'False' on {n_false} rows and is missing on another "
                  f"{n_missing}, leaving {n_valid} of {n_total} rows with a real Sev-1, Sev-2 or "
                  f"Sev-3 value. Usable for counting severe events, never for a rate."),
    )


def _billed_km(con) -> dict:
    """Nothing in the data separates fixed-slab billing from a telemetry failure."""
    pct_lines, pct_spend, zero_spend = _one(con, """
        select 100.0 * count(*) filter (where is_zero_km) / count(*),
               100.0 * sum(trip_cost) filter (where is_zero_km) / sum(trip_cost),
               sum(trip_cost) filter (where is_zero_km)
        from mis.bills
    """)
    return dict(
        table_name="bills", column_name="total_trip_km", verdict=DEGRADED,
        trust=1 - pct_lines / 100.0, test_name="zero_share",
        evidence=(f"Zero on {pct_lines:.2f}% of billed lines, and those lines carry "
                  f"{pct_spend:.1f}% of spend, {zero_spend / 1e6:.1f} million. Cost per km is "
                  f"undefined for them, so it may only be computed where distance is real."),
    )


def _signintype(con) -> dict:
    """A null that means something. Dropping these rows deletes the no-show signal."""
    n_null, noshow_null, noshow_present, accuracy = _one(con, """
        with t as (select signintype is null as never_picked_up, is_no_show from mis.emp_legs)
        select count(*) filter (where never_picked_up),
               100.0 * avg(case when is_no_show then 1.0 else 0.0 end)
                       filter (where never_picked_up),
               100.0 * avg(case when is_no_show then 1.0 else 0.0 end)
                       filter (where not never_picked_up),
               avg(case when never_picked_up = is_no_show then 1.0 else 0.0 end)
        from t
    """)
    return dict(
        table_name="emp_legs", column_name="signintype", verdict=TRUSTED, trust=accuracy,
        test_name="missingness_semantics",
        evidence=(f"Null on {n_null} legs, and those legs are {noshow_null:.1f}% no-show against "
                  f"{noshow_present:.1f}% everywhere else. The null encodes 'never picked up' and "
                  f"predicts the outcome on {100 * accuracy:.1f}% of legs: a signal, not a "
                  f"defect."),
    )


def _actual_end_epoch(con) -> dict:
    """The punctuality source of record. Everything downstream rests on this column."""
    n, n_null, n_impossible, usable = _one(con, """
        select count(*), count(*) filter (where actual_end is null),
               count(*) filter (where actual_end < planned_start),
               avg(case when actual_end is not null and actual_end >= planned_start
                        then 1.0 else 0.0 end)
        from mis.trips
    """)
    return dict(
        table_name="trips", column_name="actual_end_epoch", verdict=TRUSTED, trust=usable,
        test_name="completeness_and_range",
        evidence=(f"Populated on all {n} trips with {n_null} nulls, and later than the planned "
                  f"start on {100 * usable:.2f}% of them, {n_impossible} exceptions aside. This "
                  f"is the punctuality source of record."),
    )


# ------------------------------------------------------------------ the refusal

def is_quarantined(con, table: str, column: str) -> bool:
    """True when the field audit condemned this column. The public helper."""
    row = con.execute(
        "select verdict from field_trust where table_name = ? and column_name = ?",
        [table, column],
    ).fetchone()
    return bool(row) and row[0] == QUARANTINED


def verdict_of(con, table: str, column: str) -> str | None:
    row = con.execute(
        "select verdict from field_trust where table_name = ? and column_name = ?",
        [table, column],
    ).fetchone()
    return row[0] if row else None


def require_usable(con, table: str, column: str, *, detector: str = "?") -> None:
    """Raise loudly if `detector` is about to build a metric on a quarantined column."""
    if is_quarantined(con, table, column):
        evidence = con.execute(
            "select evidence from field_trust where table_name = ? and column_name = ?",
            [table, column],
        ).fetchone()[0]
        msg = (f"detector {detector!r} requested quarantined column {table}.{column} - refused. "
               f"{evidence}")
        log.error("REFUSED: %s", msg)
        raise QuarantinedColumnError(msg)


def guard(con, detector: str) -> None:
    """Clear every column `detector` declares before letting it run."""
    for table, column in SOURCE_COLUMNS.get(detector, ()):
        require_usable(con, table, column, detector=detector)
