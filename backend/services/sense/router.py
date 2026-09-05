"""Four GETs, mounted at /api/sense/ by backend/main.py's auto-discovery.

Read-only, and every one of them is a plain select over a table the replay already
computed - no metric is recomputed to serve a request, so the demo never waits on
anything and cannot fail live.

`json` columns are parsed before they leave: the frontend expects `evidence` to be a
list of objects, not a string containing one.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from common import db

router = APIRouter()

JSON_COLUMNS = {"evidence"}


def _fetch(sql: str, params: list) -> list[dict]:
    con = db(read_only=True)
    try:
        cur = con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        rec = dict(zip(cols, row))
        for col in JSON_COLUMNS & rec.keys():
            if isinstance(rec[col], str):
                try:
                    rec[col] = json.loads(rec[col])
                except json.JSONDecodeError:
                    pass
        out.append(rec)
    return out


def _iso(value: str | None, label: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"{label} must be ISO format, e.g. 2026-07-21")


@router.get("/field-trust")
def field_trust():
    """Every column the agent audited, worst first.

    Ordered by verdict then trust, so the three quarantined fields lead - which is the
    order a reviewer wants: what did you refuse to use, and why.
    """
    return _fetch(
        """select table_name, column_name, verdict, trust, test_name, evidence, computed_on
           from field_trust
           order by case verdict when 'quarantined' then 0 when 'degraded' then 1 else 2 end,
                    trust, table_name, column_name""", [])


@router.get("/signals")
def signals(date: str | None = None, entity_type: str | None = None,
            detector: str | None = None, severity: str | None = None,
            entity_id: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    """Raw detections, before any suppression. Worst first within a day."""
    sql = ["""select signal_id, as_of, detector, severity, entity_type, entity_id,
                     parent_id, metric, value, baseline, z, n, direction, headline,
                     evidence, created_at
              from signal where 1 = 1"""]
    params: list = []
    if date:
        sql.append("and as_of = ?")
        params.append(_iso(date, "date"))
    for col, val in (("entity_type", entity_type), ("detector", detector),
                     ("severity", severity), ("entity_id", entity_id)):
        if val:
            sql.append(f"and {col} = ?")
            params.append(val)
    sql.append("""order by as_of desc,
                  case severity when 'critical' then 0 when 'high' then 1
                                when 'medium' then 2 else 3 end,
                  abs(coalesce(z, 0)) desc
                  limit ?""")
    params.append(limit)
    return _fetch(" ".join(sql), params)


@router.get("/baselines")
def baselines(entity_type: str | None = None, entity_id: str | None = None,
              metric: str | None = None,
              # `from` is a Python keyword, so the contract's query name is an alias.
              from_: str | None = Query(None, alias="from"),
              to: str | None = None,
              limit: int = Query(2000, ge=1, le=20000)):
    """The sparkline source: one row per day per entity and metric, oldest first."""
    sql = ["""select as_of, entity_type, entity_id, parent_id, metric, value, n,
                     baseline_mean, baseline_sd, baseline_n, z, peer_group, peer_median,
                     peer_pctile, slope_28d
              from entity_baseline where 1 = 1"""]
    params: list = []
    for col, val in (("entity_type", entity_type), ("entity_id", entity_id),
                     ("metric", metric)):
        if val:
            sql.append(f"and {col} = ?")
            params.append(val)
    if from_:
        sql.append("and as_of >= ?")
        params.append(_iso(from_, "from"))
    if to:
        sql.append("and as_of <= ?")
        params.append(_iso(to, "to"))
    sql.append("order by entity_type, entity_id, metric, as_of limit ?")
    params.append(limit)
    return _fetch(" ".join(sql), params)


@router.get("/counterfactual")
def counterfactual(entity_type: str | None = None, entity_id: str | None = None,
                   lever: str | None = None, as_of: str | None = None,
                   limit: int = Query(500, ge=1, le=5000)):
    """Precomputed projections, best first. `assumption` is populated on every row."""
    sql = ["""select as_of, entity_type, entity_id, lever, param, metric,
                     baseline_value, projected_value, delta, n, assumption, confidence
              from counterfactual where 1 = 1"""]
    params: list = []
    for col, val in (("entity_type", entity_type), ("entity_id", entity_id),
                     ("lever", lever)):
        if val:
            sql.append(f"and {col} = ?")
            params.append(val)
    if as_of:
        sql.append("and as_of = ?")
        params.append(_iso(as_of, "as_of"))
    sql.append("""order by as_of desc, entity_id, lever,
                  case confidence when 'exact' then 0 when 'estimated' then 1 else 2 end,
                  delta desc
                  limit ?""")
    params.append(limit)
    return _fetch(" ".join(sql), params)
