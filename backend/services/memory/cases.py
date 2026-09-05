"""Episodic memory. One file per recurring problem, not per occurrence.

Retrieval is a SQL `where` on `signature = detector|entity_type|entity_id`. With
~14 detectors and ~50 entities that is faster, exact and explainable; a vector
store here would be pure overhead.

Every case is recomputed from the full evidence up to the replay day rather than
incremented, so running a day twice leaves the row identical.
"""
from __future__ import annotations

import datetime as dt
import json

from common import as_json, make_id, upsert

from . import verify
from .playbook import DETECTOR_LESSON

STATUS_BY_OCCURRENCES = ((4, "structural"), (2, "recurring"), (1, "open"))
RESOLVE_AFTER_DAYS = 14
RESOLVE_MIN_OBSERVED = 7   # days with a usable sample inside that window


def signature(detector: str, entity_type: str, entity_id: str) -> str:
    return f"{detector}|{entity_type}|{entity_id}"


def _status(occurrences: int) -> str:
    for floor, name in STATUS_BY_OCCURRENCES:
        if occurrences >= floor:
            return name
    return "open"


def _metric_for(con, signal_ids: list[str], detector: str) -> str | None:
    """What this case tracks. The signals the incident absorbed know best."""
    if signal_ids:
        rows = con.execute(
            f"select metric from signal where signal_id in ({','.join('?' * len(signal_ids))})",
            signal_ids).fetchall()
        for (metric,) in rows:
            if metric in verify.METRICS:
                return metric
    return {"punctuality_drop": "ota15", "metric_integrity": "ota15",
            "vendor_chronic": "ota15", "alert_ack_sla": "ack_minutes",
            "safety_cluster": "sev1_count", "noshow_spike": "noshow_pct",
            "billing_anomaly": "cost_per_trip"}.get(detector)


def _resolved(con, case: dict, day: dt.date) -> bool:
    """Within baseline for 14 consecutive days, judged against the case's own threshold."""
    row = con.execute(
        """select predicate, threshold, threshold_hi from prediction
           where case_id = ? order by made_on desc limit 1""", [case["case_id"]]).fetchone()
    if not row or not case.get("metric"):
        return False
    predicate, threshold, threshold_hi = row
    series = verify.observe(con, case["metric"], case["entity_type"], case["entity_id"],
                            day - dt.timedelta(days=RESOLVE_AFTER_DAYS - 1), day)
    if len(series) < RESOLVE_MIN_OBSERVED:
        return False   # unknown is not the same as fine
    return not any(verify.holds(predicate, v, threshold, threshold_hi) for _, v, _ in series)


def _weekday_pattern(con, case: dict, day: dt.date) -> str:
    """Name the weekdays a problem concentrates on. Words only, never a bare z-score.

    Fleet on-time is 96% on Sundays and 60% on Tuesdays, so a case that looks
    random by date is often a weekday shape.
    """
    series = verify.observe(con, case["metric"], case["entity_type"], case["entity_id"],
                            case["opened_on"] - dt.timedelta(days=28), day)
    if len(series) < 10:
        return ""
    by_day: dict[str, list[float]] = {}
    for d, v, _ in series:
        by_day.setdefault(d.strftime("%A"), []).append(v)
    means = {k: sum(v) / len(v) for k, v in by_day.items() if len(v) >= 2}
    if len(means) < 4:
        return ""
    worse_high = case["metric"] in verify.WORSE_WHEN_HIGH
    ranked = sorted(means, key=lambda k: means[k], reverse=worse_high)
    spread = abs(means[ranked[0]] - means[ranked[-1]])
    if spread < 5.0:
        return ""
    return f" The pattern concentrates on {ranked[0]}s and {ranked[1]}s."


def _diagnosis(con, case: dict, day: dt.date) -> str:
    """The current best explanation, in the register a transport manager reads.

    Every numeral here is sourced: the occurrence count is on the case row and the
    threshold and worst observation are on its predictions, which is exactly what
    the faithfulness gate checks against.
    """
    row = con.execute(
        """select predicate, threshold, observed from prediction
           where case_id = ? and observed is not null
           order by abs(observed - threshold) desc limit 1""", [case["case_id"]]).fetchone()
    metric = case.get("metric")
    label = verify.METRICS[metric]["label"] if metric in verify.METRICS else "the tracked metric"
    unit = verify.METRICS[metric]["unit"] if metric in verify.METRICS else ""
    times = "once" if case["occurrences"] == 1 else f"on {case['occurrences']} separate days"
    head = (f"{case['entity_id']} has been flagged {times} for {label} since "
            f"{case['opened_on']:%-d %B}.")
    if row:
        predicate, threshold, observed = row
        side = "above" if predicate == "gt" else "below"
        head += (f" It has since run {side} the {threshold}{unit} line, "
                 f"as far as {round(observed, 1)}{unit}.")
    return head + _weekday_pattern(con, case, day) + " " + DETECTOR_LESSON.get(
        case["detector"], "Watch the entity's own trailing same-weekday baseline, not the fleet mean.")


def collect(con, day: dt.date) -> list[dict]:
    """Every case as it stands on `day`, rebuilt from the incidents behind it.

    An occurrence is a day the problem surfaced: an incident opening, or a signal
    that reason folded back into this case as a known pattern. The fourth Cedar
    Ridge firing is a reclassification, not a new incident, and still counts.
    """
    incidents = con.execute(
        """select incident_id, opened_on, detector, entity_type, entity_id, signal_ids
           from incident where opened_on <= ? order by opened_on, incident_id""", [day]).fetchall()
    reclassified = con.execute(
        """select parent_incident_id, as_of from suppression
           where reason_code = 'known_pattern' and as_of <= ? and parent_incident_id is not null""",
        [day]).fetchall()

    cases: dict[str, dict] = {}
    for incident_id, opened_on, detector, entity_type, entity_id, signal_ids in incidents:
        sig = signature(detector, entity_type, entity_id)
        case = cases.setdefault(sig, {
            "case_id": make_id("case", sig), "signature": sig, "detector": detector,
            "entity_type": entity_type, "entity_id": entity_id,
            "incident_ids": [], "signal_ids": [], "occurrence_days": set(),
        })
        case["incident_ids"].append(incident_id)
        case["signal_ids"].extend(json.loads(signal_ids or "[]"))
        case["occurrence_days"].add(opened_on)
    by_incident = {i: sig for sig, c in cases.items() for i in c["incident_ids"]}
    for parent, as_of in reclassified:
        sig = by_incident.get(parent)
        if sig:
            cases[sig]["occurrence_days"].add(as_of)

    out = []
    for case in cases.values():
        days = sorted(case.pop("occurrence_days"))
        case["opened_on"] = days[0]
        case["last_seen_on"] = days[-1]
        case["occurrences"] = len(days)
        case["metric"] = _metric_for(con, case.pop("signal_ids"), case["detector"])
        case["status"] = _status(case["occurrences"])
        if _resolved(con, case, day):
            case["status"] = "resolved"
        out.append(case)
    return out


def write(con, day: dt.date, cases: list[dict]) -> int:
    stamp = dt.datetime.combine(day, dt.time(23, 59, 40))
    rows = []
    for case in cases:
        rows.append({
            "case_id": case["case_id"], "signature": case["signature"],
            "entity_type": case["entity_type"], "entity_id": case["entity_id"],
            "opened_on": case["opened_on"], "last_seen_on": case["last_seen_on"],
            "occurrences": case["occurrences"], "status": case["status"],
            "incident_ids": as_json(case["incident_ids"]),
            "diagnosis": _diagnosis(con, case, day),
            "created_at": dt.datetime.combine(case["opened_on"], dt.time(23, 59, 40)),
            "updated_at": stamp,
        })
    return upsert(con, "case_file", rows, key="case_id")
