"""HTTP contract for reasoning incidents, suppressions, and what-if answers."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from common import LAST_DAY, REPLAY_START, db


router = APIRouter()


class WhatIfRequest(BaseModel):
    incident_id: str = Field(min_length=1)
    lever: str = Field(min_length=1)
    param: str = Field(min_length=1)


@router.get("/incidents")
def incidents(
    date_: date | None = Query(default=None, alias="date"),
    status: str | None = None,
    persona: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date_ is not None:
        clauses.append("opened_on = ?")
        params.append(date_)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if persona is not None:
        clauses.append("persona = ?")
        params.append(persona)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(limit)
    return _read_rows(
        f"""select * from incident {where}
            order by opened_on desc, created_at desc, incident_id
            limit ?""",
        params,
        json_fields=("context", "signal_ids", "recommendation"),
    )


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str) -> dict[str, Any]:
    con = db(read_only=True)
    try:
        incident = _one_row(
            con,
            "select * from incident where incident_id = ?",
            [incident_id],
            json_fields=("context", "signal_ids", "recommendation"),
        )
        if incident is None:
            raise HTTPException(404, f"incident {incident_id!r} not found")
        incident["hypotheses"] = _rows(
            con,
            """select * from hypothesis where incident_id = ?
               order by rank, hypothesis_id""",
            [incident_id],
            json_fields=("result",),
        )
        signal_ids = incident.get("signal_ids") or []
        if signal_ids:
            placeholders = ", ".join("?" for _ in signal_ids)
            signal_rows = _rows(
                con,
                f"""select * from signal where signal_id in ({placeholders})
                    order by created_at, signal_id""",
                list(signal_ids),
                json_fields=("evidence",),
            )
            by_id = {row["signal_id"]: row for row in signal_rows}
            incident["signals"] = [
                by_id[signal_id] for signal_id in signal_ids if signal_id in by_id
            ]
        else:
            incident["signals"] = []
        return incident
    finally:
        con.close()


@router.get("/suppressions")
def suppressions(
    date_: date | None = Query(default=None, alias="date"),
    reason_code: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date_ is not None:
        clauses.append("sp.as_of = ?")
        params.append(date_)
    if reason_code is not None:
        clauses.append("sp.reason_code = ?")
        params.append(reason_code)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(limit)
    con = db(read_only=True)
    try:
        rows = _rows(
            con,
            f"""select sp.*, s.signal_id as joined_signal_id, s.as_of as signal_as_of,
                       s.detector as signal_detector, s.severity as signal_severity,
                       s.entity_type as signal_entity_type, s.entity_id as signal_entity_id,
                       s.parent_id as signal_parent_id, s.metric as signal_metric,
                       s.value as signal_value, s.baseline as signal_baseline,
                       s.z as signal_z, s.n as signal_n, s.direction as signal_direction,
                       s.headline as signal_headline, s.evidence as signal_evidence,
                       s.created_at as signal_created_at
                from suppression sp
                left join signal s on s.signal_id = sp.signal_id
                {where}
                order by sp.as_of desc, sp.created_at desc, sp.suppression_id
                limit ?""",
            params,
            json_fields=("evidence", "signal_evidence"),
        )
    finally:
        con.close()
    return [_nest_signal(row) for row in rows]


@router.get("/summary")
def summary(date_: date | None = Query(default=None, alias="date")) -> dict[str, Any]:
    con = db(read_only=True)
    try:
        selected = date_ or _latest_signal_day(con) or REPLAY_START
        raw_signals = _count(con, "select count(*) from signal where as_of = ?", [selected])
        suppressed = _count(
            con, "select count(*) from suppression where as_of = ?", [selected]
        )
        incident_count = _count(
            con, "select count(*) from incident where opened_on = ?", [selected]
        )
        reason_rows = con.execute(
            """select reason_code, count(*) from suppression
               where as_of = ? group by reason_code order by reason_code""",
            [selected],
        ).fetchall()
        window = {
            "raw_signals": _count(
                con,
                "select count(*) from signal where as_of between ? and ?",
                [REPLAY_START, LAST_DAY],
            ),
            "suppressed": _count(
                con,
                "select count(*) from suppression where as_of between ? and ?",
                [REPLAY_START, LAST_DAY],
            ),
            "incidents": _count(
                con,
                "select count(*) from incident where opened_on between ? and ?",
                [REPLAY_START, LAST_DAY],
            ),
        }
        return {
            "date": str(selected),
            "raw_signals": raw_signals,
            "suppressed": suppressed,
            "incidents": incident_count,
            "by_reason": {str(reason): int(count) for reason, count in reason_rows},
            "window": window,
        }
    finally:
        con.close()


@router.post("/whatif")
def whatif(request: WhatIfRequest) -> dict[str, Any]:
    con = db(read_only=True)
    try:
        incident = _one_row(
            con,
            "select * from incident where incident_id = ?",
            [request.incident_id],
            json_fields=("signal_ids",),
        )
        if incident is None:
            raise HTTPException(404, f"incident {request.incident_id!r} not found")

        signal_ids = incident.get("signal_ids") or []
        signals: list[dict[str, Any]] = []
        if signal_ids:
            placeholders = ", ".join("?" for _ in signal_ids)
            signals = _rows(
                con,
                f"select * from signal where signal_id in ({placeholders})",
                list(signal_ids),
                json_fields=("evidence",),
            )
        metric = signals[0].get("metric") if signals else None
        n = signals[0].get("n") if signals else 0
        entity_ids = _counterfactual_entities(incident, signals)
        candidates = _rows(
            con,
            "select * from counterfactual order by as_of desc, entity_id, lever, param",
            [],
        )
        if not candidates:
            baseline = signals[0].get("value") if signals else None
            return {
                "lever": request.lever,
                "param": request.param,
                "metric": metric,
                "baseline_value": baseline,
                "projected_value": baseline,
                "delta": 0.0,
                "n": n,
                "confidence": "weak",
                "assumption": "No precomputed counterfactual is available.",
                "narrative": (
                    "No precomputed counterfactual is available for this request, so no "
                    "effect is inferred and the change is not recommended."
                ),
            }

        chosen = min(
            candidates,
            key=lambda row: _counterfactual_distance(
                row, entity_ids, metric, request.lever, request.param
            ),
        )
        exact = (
            chosen["entity_id"] in entity_ids
            and chosen["lever"] == request.lever
            and str(chosen["param"]) == request.param
            and (metric is None or chosen["metric"] == metric)
        )
        response = {
            key: chosen[key]
            for key in (
                "lever",
                "param",
                "metric",
                "baseline_value",
                "projected_value",
                "delta",
                "n",
                "confidence",
                "assumption",
            )
        }
        response["narrative"] = _whatif_narrative(chosen, exact)
        return response
    finally:
        con.close()


def _read_rows(
    sql: str,
    params: list[Any],
    json_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    con = db(read_only=True)
    try:
        return _rows(con, sql, params, json_fields=json_fields)
    finally:
        con.close()


def _rows(
    con: Any,
    sql: str,
    params: list[Any],
    json_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        for field in json_fields:
            if field in row:
                row[field] = _json_value(row[field])
    return rows


def _one_row(
    con: Any,
    sql: str,
    params: list[Any],
    json_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    rows = _rows(con, sql, params, json_fields=json_fields)
    return rows[0] if rows else None


def _count(con: Any, sql: str, params: list[Any]) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _latest_signal_day(con: Any) -> date | None:
    row = con.execute("select max(as_of) from signal").fetchone()
    return row[0] if row else None


def _nest_signal(row: dict[str, Any]) -> dict[str, Any]:
    signal_fields = {
        "signal_id": row.pop("joined_signal_id", None),
        "as_of": row.pop("signal_as_of", None),
        "detector": row.pop("signal_detector", None),
        "severity": row.pop("signal_severity", None),
        "entity_type": row.pop("signal_entity_type", None),
        "entity_id": row.pop("signal_entity_id", None),
        "parent_id": row.pop("signal_parent_id", None),
        "metric": row.pop("signal_metric", None),
        "value": row.pop("signal_value", None),
        "baseline": row.pop("signal_baseline", None),
        "z": row.pop("signal_z", None),
        "n": row.pop("signal_n", None),
        "direction": row.pop("signal_direction", None),
        "headline": row.pop("signal_headline", None),
        "evidence": row.pop("signal_evidence", None),
        "created_at": row.pop("signal_created_at", None),
    }
    row["signal"] = signal_fields if signal_fields["signal_id"] else None
    return row


def _counterfactual_entities(
    incident: dict[str, Any], signals: list[dict[str, Any]]
) -> list[str]:
    entity_ids = [str(incident["entity_id"])]
    if incident.get("entity_type") == "office" and " / " in incident["entity_id"]:
        entity_ids.append(str(incident["entity_id"]).split(" / ", 1)[0])
    for signal in signals:
        parent_id = signal.get("parent_id")
        if parent_id and str(parent_id) not in entity_ids:
            entity_ids.append(str(parent_id))
    return entity_ids


def _counterfactual_distance(
    row: dict[str, Any],
    entity_ids: list[str],
    metric: str | None,
    lever: str,
    param: str,
) -> tuple[int, int, int, int, float]:
    return (
        0 if row["entity_id"] in entity_ids else 1,
        0 if row["lever"] == lever else 1,
        0 if str(row["param"]) == param else 1,
        0 if metric is None or row["metric"] == metric else 1,
        abs(float(row.get("delta") or 0.0)),
    )


def _whatif_narrative(row: dict[str, Any], exact: bool) -> str:
    unit = "%" if row.get("metric") in {"ota15", "noshow_pct"} else ""
    before = _plain(row.get("baseline_value"))
    after = _plain(row.get("projected_value"))
    delta = _plain(row.get("delta"))
    prefix = "" if exact else "The requested combination is unavailable; this is the closest precomputed option. "
    if float(row.get("delta") or 0.0) <= 0:
        conclusion = "The result is worse, so this change is not recommended."
    else:
        conclusion = "The result improves, but it remains a projection under the stated assumption."
    return (
        f"{prefix}{row['lever']} with {row['param']} moves {row['metric']} from "
        f"{before}{unit} to {after}{unit}, a {delta}{unit} change. {conclusion} "
        f"Assumption: {row['assumption']}"
    )


def _plain(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
