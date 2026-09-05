"""Four gates, all deterministic. No LLM anywhere in this service, by design —
a rerun reproduces every number, which is the whole answer to "how do you know
it isn't making this up".
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics

from common import LAST_DAY, as_json, now, upsert

from . import probes

# ------------------------------------------------------------------ gate 1
# Ignore years, dates and ordinals; everything else in a sentence a human reads
# must trace to a value in the evidence attached to that row.
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
_DATE_LIKE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}\s+(?:{_MONTHS})\b|\b(?:{_MONTHS})\s+\d{{1,2}}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b", re.IGNORECASE)
# The lookbehind keeps `Sev-1`, `CASE-0001` and the tail of an already-matched
# decimal out of the extraction; a genuine minus sign after a space still counts.
_NUMBER = re.compile(r"(?<![\w.-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.-])-?\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
TOLERANCE = 0.05


def numbers_in(text: str) -> list[str]:
    """Every numeral a reader would treat as a claim."""
    return [m.group(0) for m in _NUMBER.finditer(_DATE_LIKE.sub(" ", text or ""))]


def _sourced(literal: str, pool: set[float]) -> bool:
    raw = literal.replace(",", "")
    value = float(raw)
    if "." not in raw and 1900 <= value <= 2100:
        return True                       # a year, not a measurement
    decimals = len(raw.partition(".")[2])
    return any(abs(v - value) <= TOLERANCE or round(v, decimals) == value for v in pool)


def _leaves(obj) -> list[float]:
    """Every numeric leaf of a json column — a claim's evidence is often nested."""
    if isinstance(obj, bool) or obj is None:
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _leaves(x)]
    if isinstance(obj, list):
        return [v for x in obj for v in _leaves(x)]
    if isinstance(obj, str):
        return []
    return []


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _pools(con) -> tuple[dict, dict, dict]:
    """Build the evidence pool for every incident, suppression and case."""
    signal_evidence = {sid: _leaves(_json(ev))
                       for sid, ev in con.execute("select signal_id, evidence from signal").fetchall()}
    hyp = {}
    for iid, result in con.execute("select incident_id, result from hypothesis").fetchall():
        hyp.setdefault(iid, []).extend(_leaves(_json(result)))
    child = {}
    supp_own = {}
    for sup_id, parent, ev in con.execute(
            "select suppression_id, parent_incident_id, evidence from suppression").fetchall():
        values = _leaves(_json(ev))
        supp_own[sup_id] = (parent, set(values))
        if parent:
            child.setdefault(parent, []).extend(values)

    incidents = {}
    for iid, ctx, sids, rec in con.execute(
            "select incident_id, context, signal_ids, recommendation from incident").fetchall():
        pool = set(_leaves(_json(ctx))) | set(_leaves(_json(rec)))
        for sid in _json(sids) or []:
            pool |= set(signal_evidence.get(sid, []))
        pool |= set(hyp.get(iid, [])) | set(child.get(iid, []))
        incidents[iid] = pool

    suppressions = {sid: own | incidents.get(parent, set())
                    for sid, (parent, own) in supp_own.items()}

    cases = {}
    for cid, iids, occurrences in con.execute(
            "select case_id, incident_ids, occurrences from case_file").fetchall():
        pool = {float(occurrences)}
        for iid in _json(iids) or []:
            pool |= incidents.get(iid, set())
        cases[cid] = pool
    for cid, threshold, hi, observed in con.execute(
            "select case_id, threshold, threshold_hi, observed from prediction").fetchall():
        pool = cases.setdefault(cid, set())
        pool |= {float(v) for v in (threshold, hi, observed) if v is not None}
    return incidents, suppressions, cases


def faithfulness(con) -> dict:
    incidents, suppressions, cases = _pools(con)
    checked = [
        ("select incident_id, headline || ' ' || narrative from incident", incidents),
        ("select suppression_id, explanation from suppression", suppressions),
        ("select case_id, diagnosis from case_file", cases),
    ]
    statements = extracted = 0
    unsourced = []
    for sql, pools in checked:
        for key, text in con.execute(sql).fetchall():
            pool = pools.get(key, set())
            statements += len([s for s in _SENTENCE.split(text or "") if s.strip()])
            for literal in numbers_in(text):
                extracted += 1
                if not _sourced(literal, pool):
                    unsourced.append({"row": key, "number": literal})
    for pid, statement, threshold, hi, observed in con.execute(
            "select prediction_id, statement, threshold, threshold_hi, observed from prediction").fetchall():
        pool = {float(v) for v in (threshold, hi, observed) if v is not None}
        statements += len([s for s in _SENTENCE.split(statement or "") if s.strip()])
        for literal in numbers_in(statement):
            extracted += 1
            if not _sourced(literal, pool):
                unsourced.append({"row": pid, "number": literal})
    rate = round(len(unsourced) / extracted, 4) if extracted else 0.0
    return {"metric": "unsourced_number_rate", "value": rate, "passed": rate == 0.0,
            "detail": {"statements_checked": statements, "numbers_extracted": extracted,
                       "unsourced": len(unsourced), "examples": unsourced[:20],
                       "note": "Every numeral in every narrative, explanation, diagnosis and "
                               "prediction, matched to a value in that row's evidence within "
                               "rounding tolerance. Years, dates and ordinals are excluded."}}


# ------------------------------------------------------------------ gate 2
# Ground truth is computed from July alone. Deriving it from the full window
# leaks the answer into the training months and the number stops meaning anything.
JULY = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
MIN_TRIPS = 40
MONTH_END_DROP = 5.0


def _july_truth(con) -> dict[tuple[str, str], float]:
    degraded = {}
    for entity_type, expr in (("vendor", "vendor"),
                              ("office", "business_unit || ' / ' || office")):
        rows = con.execute(
            f"""select {expr}, 100.0 * count(*) filter (where is_ontime_15) / count(*)
                from mis.trips where trip_date between ? and ?
                group by 1 having count(*) >= {MIN_TRIPS}""", list(JULY)).fetchall()
        values = [v for _, v in rows]
        if len(values) < 3:
            continue
        cut = statistics.median(values) - statistics.stdev(values)
        for entity_id, value in rows:
            if value < cut:
                degraded[(entity_type, entity_id)] = value
    return degraded


def _month_end_catch(con, entity_type: str, entity_id: str) -> dt.date:
    """The earliest a month-end report could have caught this entity.

    Consecutive months with a drop of more than five points. An entity that was
    always bad never trips that test, so the report never catches it and the
    window end is the honest floor on the agent's lead.
    """
    expr = "vendor" if entity_type == "vendor" else "business_unit || ' / ' || office"
    rows = con.execute(
        f"""select date_trunc('month', trip_date)::date m,
                   100.0 * count(*) filter (where is_ontime_15) / count(*)
            from mis.trips where {expr} = ? group by 1 having count(*) >= {MIN_TRIPS} order by 1""",
        [entity_id]).fetchall()
    for (m1, v1), (m2, v2) in zip(rows, rows[1:]):
        if (m2 - m1).days <= 31 and v2 < v1 - MONTH_END_DROP:
            return (m2.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return LAST_DAY


def detection(con, day: dt.date) -> list[dict]:
    truth = _july_truth(con)
    flagged = {(t, e): d for t, e, d in con.execute(
        """select entity_type, entity_id, min(opened_on) from incident
           where entity_type in ('vendor', 'office') and opened_on <= ?
           group by 1, 2""", [day]).fetchall()}
    hits = set(truth) & set(flagged)
    precision = len(hits) / len(flagged) if flagged else 0.0
    recall = len(hits) / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    leads = [( _month_end_catch(con, *k) - flagged[k]).days for k in hits]
    median_lead = float(statistics.median(leads)) if leads else 0.0
    shared = {"flagged": len(flagged), "actually_degraded": len(truth), "true_positives": len(hits),
              "ground_truth": "July on-time more than one standard deviation below the peer-group "
                              "median, computed from July data only, n >= 40 trips"}
    return [
        {"metric": "precision", "value": round(precision, 3), "passed": precision >= 0.5,
         "detail": shared | {"entities": sorted(f"{t}:{e}" for t, e in hits)}},
        {"metric": "recall", "value": round(recall, 3), "passed": recall >= 0.5, "detail": shared},
        {"metric": "f1", "value": round(f1, 3), "passed": f1 >= 0.5, "detail": shared},
        {"metric": "median_lead_days", "value": median_lead, "passed": median_lead > 0,
         "detail": shared | {
             "leads": sorted(leads),
             "baseline": "month-end report comparing consecutive months, drop of more than 5 points",
             "note": "Entities that were bad from the first week never trip a month-on-month "
                     f"drop test, so their baseline catch date is capped at {LAST_DAY}."}},
    ]


# ------------------------------------------------------------------ gate 3
MONEY_MINUTES_PEOPLE = ("employees", "people", "riders", "min", "minutes", "hours",
                        "rs", "inr", "rupees", "trips", "alerts", "legs")


def _trace_ok(con, incident_id, context, recommendation) -> tuple[bool, list[str]]:
    fails = []
    hyps = con.execute(
        "select verdict, result from hypothesis where incident_id = ?", [incident_id]).fetchall()
    if len(hyps) < 2:
        fails.append("fewer than 2 hypotheses")
    if not any(v == "refuted" and _json(r) for v, r in hyps):
        fails.append("no refuted hypothesis with a result")
    ctx = _json(context) or {}
    for key in ("trend", "peer", "threshold", "impact"):
        if not ctx.get(key):
            fails.append(f"context.{key} missing")
    rec = _json(recommendation) or {}
    for key in ("action", "owner", "due"):
        if not rec.get(key):
            fails.append(f"recommendation.{key} missing")
    impact = ctx.get("impact") or {}
    if not isinstance(impact.get("value"), (int, float)) or isinstance(impact.get("value"), bool):
        fails.append("impact.value not numeric")
    elif not any(u in str(impact.get("unit", "")).lower() for u in MONEY_MINUTES_PEOPLE):
        fails.append("impact.unit is not money, minutes or people")
    return not fails, fails


def trace_schema(con, day: dt.date) -> dict:
    rows = con.execute(
        "select incident_id, context, recommendation from incident where opened_on <= ?",
        [day]).fetchall()
    results = [(iid, *_trace_ok(con, iid, ctx, rec)) for iid, ctx, rec in rows]
    complete = sum(1 for _, ok, _ in results if ok)
    fraction = round(complete / len(results), 4) if results else 0.0
    return {"metric": "complete_traces", "value": fraction, "passed": bool(results) and complete == len(results),
            "detail": {"incidents": len(results), "complete": complete,
                       "failures": {iid: fails for iid, ok, fails in results if not ok},
                       "checks": ["at least 2 hypotheses", "at least 1 refuted with a result",
                                  "all 4 context keys populated",
                                  "recommendation has action, owner and due",
                                  "impact.value numeric in money, minutes or people"]}}


# ------------------------------------------------------------------ gate 4
def behaviour(con) -> dict:
    results = probes.run(con)
    passed = [p for p in results if p["passed"]]
    failed = [p["name"] for p in results if not p["passed"]]
    return {"metric": "probes_passed", "value": float(len(passed)),
            "passed": not failed,
            "detail": {"total": len(results), "passed": len(passed), "failed": failed,
                       "probes": results}}


# ------------------------------------------------------------------ writer
def run(con, day: dt.date) -> int:
    run_id = str(day)
    stamp = now()
    rows = []
    for gate, results in (("faithfulness", [faithfulness(con)]),
                          ("detection", detection(con, day)),
                          ("trace_schema", [trace_schema(con, day)]),
                          ("behaviour", [behaviour(con)])):
        for r in results:
            rows.append({"run_id": run_id, "gate": gate, "metric": r["metric"],
                         "value": r["value"], "passed": r["passed"],
                         "detail": as_json(r["detail"]), "computed_at": stamp})
    return upsert(con, "eval_result", rows, key=("run_id", "gate", "metric"))
