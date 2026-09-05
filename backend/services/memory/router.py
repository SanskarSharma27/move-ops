"""Read-only HTTP surface for memory. Mounted at /api/memory automatically.

`json` columns arrive as parsed objects, never strings, and composite endpoints
are assembled here so the UI never has to join.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from common import db

router = APIRouter()

OUTCOMES = ("confirmed", "refuted", "unverifiable")


def _rows(sql: str, params: list, json_cols: tuple[str, ...] = ()) -> list[dict]:
    con = db(read_only=True)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()
    for row in out:
        for col in json_cols:
            if isinstance(row.get(col), str):
                row[col] = json.loads(row[col])
    return out


@router.get("/cases")
def cases(entity_id: str | None = None, status: str | None = None, limit: int = 50):
    where, params = ["1 = 1"], []
    if entity_id:
        where.append("entity_id = ?")
        params.append(entity_id)
    if status:
        where.append("status = ?")
        params.append(status)
    return _rows(f"""select * from case_file where {' and '.join(where)}
                     order by occurrences desc, last_seen_on desc limit ?""",
                 [*params, limit], ("incident_ids",))


@router.get("/cases/{case_id}")
def case(case_id: str):
    found = _rows("select * from case_file where case_id = ?", [case_id], ("incident_ids",))
    if not found:
        raise HTTPException(404, f"no case {case_id}")
    case = found[0]
    case["predictions"] = _rows(
        "select * from prediction where case_id = ? order by made_on", [case_id])
    record = {o: sum(1 for p in case["predictions"] if p["outcome"] == o) for o in OUTCOMES}
    record["pending"] = sum(1 for p in case["predictions"] if p["outcome"] is None)
    case["prediction_record"] = record
    return case


@router.get("/predictions")
def predictions(status: str | None = None, case_id: str | None = None):
    where, params = ["1 = 1"], []
    if status:
        where.append("outcome is null" if status == "pending" else "outcome = ?")
        if status != "pending":
            params.append(status)
    if case_id:
        where.append("case_id = ?")
        params.append(case_id)
    return _rows(f"select * from prediction where {' and '.join(where)} order by made_on desc",
                 params)


@router.get("/playbook")
def playbook():
    return _rows("select * from playbook order by promoted_on, confidence desc", [], ("evidence",))


@router.get("/eval")
def evaluations(gate: str | None = None, run_id: str | None = None):
    where, params = ["1 = 1"], []
    if gate:
        where.append("gate = ?")
        params.append(gate)
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    return _rows(f"select * from eval_result where {' and '.join(where)} order by gate, metric",
                 params, ("detail",))


@router.get("/report-card")
def report_card():
    """The four numbers that stay on screen. Latest completed evaluation run."""
    latest = _rows("select run_id, max(computed_at) as computed_at from eval_result "
                   "group by 1 order by 2 desc limit 1", [])
    if not latest:
        raise HTTPException(404, "no evaluation run yet — run `make replay`")
    run_id = latest[0]["run_id"]
    rows = _rows("select * from eval_result where run_id = ?", [run_id], ("detail",))
    by_gate = {}
    for row in rows:
        by_gate.setdefault(row["gate"], {})[row["metric"]] = row

    def value(gate, metric, default=None):
        row = by_gate.get(gate, {}).get(metric)
        return default if row is None else row["value"]

    def detail(gate, metric, key, default=None):
        row = by_gate.get(gate, {}).get(metric)
        return default if row is None else (row["detail"] or {}).get(key, default)

    def passed(gate):
        entries = by_gate.get(gate, {}).values()
        return bool(entries) and all(e["passed"] for e in entries)

    return {
        "run_id": run_id,
        "generated_at": latest[0]["computed_at"],
        "faithfulness": {
            "unsourced_number_rate": value("faithfulness", "unsourced_number_rate", 0.0),
            "statements_checked": detail("faithfulness", "unsourced_number_rate", "statements_checked", 0),
            "numbers_extracted": detail("faithfulness", "unsourced_number_rate", "numbers_extracted", 0),
            "passed": passed("faithfulness"),
        },
        "detection": {
            "precision": value("detection", "precision", 0.0),
            "recall": value("detection", "recall", 0.0),
            "median_lead_days": value("detection", "median_lead_days", 0.0),
            "passed": passed("detection"),
        },
        "trace_schema": {
            "complete_traces": value("trace_schema", "complete_traces", 0.0),
            "incidents": detail("trace_schema", "complete_traces", "incidents", 0),
            "complete": detail("trace_schema", "complete_traces", "complete", 0),
            "passed": passed("trace_schema"),
        },
        "behaviour": {
            "probes_passed": int(value("behaviour", "probes_passed", 0) or 0),
            "probes_total": detail("behaviour", "probes_passed", "total", 0),
            "failed": detail("behaviour", "probes_passed", "failed", []),
            "passed": passed("behaviour"),
        },
    }
