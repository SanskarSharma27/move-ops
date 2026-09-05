"""Daily orchestration for the reasoning service."""
from __future__ import annotations

from datetime import date
from typing import Any

from common import upsert

from .correlate import correlate
from .hypotheses import build_hypotheses
from .narrate import build_incident, structuralize_recommendation


def run_day(con: Any, day: date) -> None:
    """Reason over one business date idempotently."""

    # Keep these statements in autocommit mode. DuckDB 1.1 retains primary-key
    # index entries until an explicit transaction ends, so delete-then-insert
    # (the shared upsert contract) cannot replace a row inside one outer transaction.
    _clear_day(con, day)
    signals = _rows(
        con,
        """select * from signal where as_of = ?
           order by created_at, signal_id""",
        [day],
    )
    decisions = correlate(con, day, signals)

    for incident_id in decisions.structural_incident_ids:
        row = con.execute(
            "select recommendation from incident where incident_id = ?",
            [incident_id],
        ).fetchone()
        if row:
            con.execute(
                """update incident
                   set status = 'structural', recommendation = ?
                   where incident_id = ?""",
                [structuralize_recommendation(row[0]), incident_id],
            )

    incident_rows: list[dict[str, Any]] = []
    hypothesis_rows: list[dict[str, Any]] = []
    for candidate in decisions.incidents:
        hypotheses = build_hypotheses(con, candidate, day)
        _assert_trace(candidate.incident_id, hypotheses)
        incident_rows.append(build_incident(con, candidate, day, hypotheses))
        hypothesis_rows.extend(hypotheses)

    upsert(con, "incident", incident_rows, key="incident_id")
    upsert(con, "suppression", decisions.suppressions, key="suppression_id")
    upsert(con, "hypothesis", hypothesis_rows, key="hypothesis_id")


def _clear_day(con: Any, day: date) -> None:
    """Remove only B-owned outputs derived from this day before rebuilding them."""

    con.execute(
        """delete from hypothesis
           where incident_id in (
             select incident_id from incident where opened_on = ?
           )""",
        [day],
    )
    con.execute("delete from suppression where as_of = ?", [day])
    con.execute("delete from incident where opened_on = ?", [day])


def _rows(con: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _assert_trace(incident_id: str, hypotheses: list[dict[str, Any]]) -> None:
    if len(hypotheses) < 2:
        raise ValueError(f"incident {incident_id} requires at least two hypotheses")
    if not any(row["verdict"] == "refuted" for row in hypotheses):
        raise ValueError(f"incident {incident_id} requires a refuted hypothesis")
