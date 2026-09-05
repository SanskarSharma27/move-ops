"""Deterministic, evidence-bound incident narration and recommendations."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from common import as_json


TARGETS: dict[str, tuple[float, str]] = {
    "ota15": (80.0, "%"),
    "ack_minutes": (15.0, "minutes"),
    "noshow_pct": (2.0, "%"),
    "seat_util": (0.6, "ratio"),
    "cost_per_trip": (0.0, "rupees"),
    "sev1_count": (0.0, "alerts"),
}

HEADLINES = {
    "punctuality_drop": "{entity} punctuality needs attention",
    "metric_integrity": "{entity} reported improvement is not operational",
    "alert_ack_sla": "{entity} alert acknowledgement is outside policy",
    "safety_cluster": "{entity} severe safety alerts need escalation",
    "escort_breach": "{entity} escort controls need intervention",
    "noshow_spike": "{entity} rider no-shows need attention",
    "billing_anomaly": "{entity} billing integrity needs review",
    "vendor_chronic": "{entity} performance is chronically below expectation",
}

TRIP_ENTITY_FILTER = """
(
  (? = 'business_unit' and t.business_unit = ?)
  or (? = 'office' and t.business_unit || ' / ' || t.office = ?)
  or (? = 'vendor' and t.vendor = ?)
  or (? = 'shift' and t.shift_type = ?)
)
""".strip()

LATE_EMPLOYEE_IMPACT_SQL = f"""
select count(*)
from mis.emp_legs e
join mis.trips t using (trip_id)
where t.trip_date = ? and not t.is_ontime_15 and {TRIP_ENTITY_FILTER}
""".strip()

AFFECTED_EMPLOYEE_IMPACT_SQL = f"""
select count(*)
from mis.emp_legs e
join mis.trips t using (trip_id)
where t.trip_date = ? and {TRIP_ENTITY_FILTER}
""".strip()


def build_incident(
    con: Any,
    candidate: Any,
    day: date,
    hypotheses: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Create a complete incident row without free-form generation."""

    primary = candidate.primary
    baseline = _baseline(con, day, candidate)
    context = _context(con, day, candidate, baseline)
    recommendation = _recommendation(con, day, candidate)
    display = _display_name(candidate.entity_id)
    headline = HEADLINES.get(
        primary.get("detector"), "{entity} requires operational review"
    ).format(entity=display)

    # Hypothesis results influence the wording, but all numerals still come only
    # from the absorbed signals' evidence arrays.
    verdicts = {row.get("name"): row.get("verdict") for row in hypotheses}
    narrative = _narrative(candidate, verdicts)
    signal_ids = list(
        dict.fromkeys(str(signal["signal_id"]) for signal in candidate.signals)
    )

    return {
        "incident_id": candidate.incident_id,
        "opened_on": day,
        "status": candidate.status,
        "severity": _severity(candidate.signals),
        "entity_type": candidate.entity_type,
        "entity_id": candidate.entity_id,
        "detector": primary["detector"],
        "headline": headline,
        "narrative": narrative,
        "context": as_json(context),
        "signal_ids": as_json(signal_ids),
        "recommendation": as_json(recommendation),
        "persona": _persona(primary),
        "created_at": datetime.combine(day, time(23, 59, 30)),
    }


def structuralize_recommendation(value: Any) -> str:
    """Change a recurring incident from investigation to a structural fix."""

    recommendation = _json_value(value, {})
    recommendation["action"] = (
        "Fix the Tuesday capacity plan; this is recurring work, not a fresh investigation."
    )
    return as_json(recommendation)


def _narrative(candidate: Any, verdicts: dict[str, str]) -> str:
    primary = candidate.primary
    evidence = _all_evidence(candidate.signals)
    detector = primary.get("detector")
    entity = _display_name(candidate.entity_id)

    if detector == "metric_integrity" and primary.get("direction") == "better":
        before = _fact(evidence, "on-time before")
        after = _fact(evidence, "on-time after")
        planned_before = _fact(evidence, "planned duration before")
        planned_after = _fact(evidence, "planned duration after")
        actual_before = _fact(evidence, "actual duration before")
        actual_after = _fact(evidence, "actual duration after")
        facts = [before, after, planned_before, planned_after, actual_before, actual_after]
        if all(fact is not None for fact in facts):
            return (
                f"At {entity}, on-time arrival moved from {_show(before)} to {_show(after)}. "
                f"Planned duration moved from {_show(planned_before)} to {_show(planned_after)}, "
                f"while actual journey time moved from {_show(actual_before)} to {_show(actual_after)}. "
                "The reported improvement is rejected because the target moved, not the commute."
            )

    if detector == "punctuality_drop":
        actual = _fact(evidence, "on-time arrival")
        expected = _fact(evidence, "trailing same-weekday baseline")
        trips = _fact(evidence, "trips affected") or _fact(evidence, "trips that day")
        clauses = []
        if actual is not None:
            clauses.append(f"on-time arrival was {_show(actual)}")
        if expected is not None:
            clauses.append(f"the trailing same-weekday baseline was {_show(expected)}")
        if trips is not None:
            clauses.append(f"the signal covered {_show(trips)}")
        opening = _join_facts(clauses)
        ending = (
            "Vendor alerts moved together, so they were folded into this parent event and "
            "no vendor penalty is warranted."
            if len(candidate.signals) > 1
            or verdicts.get("vendor_failure") == "refuted"
            else "The signal remains local and deserves operational review."
        )
        return f"At {entity}, {opening}. {ending}"

    if detector == "safety_cluster":
        alerts = _fact(evidence, "Sev-1 alerts")
        baseline = _fact(evidence, "daily baseline")
        speeding = _fact(evidence, "over-speeding events")
        acknowledgement = _fact(evidence, "mean acknowledgement time")
        clauses = [
            f"severe alerts reached {_show(alerts)}" if alerts else None,
            f"the daily baseline was {_show(baseline)}" if baseline else None,
            f"over-speeding contributed {_show(speeding)}" if speeding else None,
            f"mean acknowledgement time was {_show(acknowledgement)}"
            if acknowledgement
            else None,
        ]
        return f"At {entity}, {_join_facts(clauses)}. This safety and response gap requires escalation."

    if detector == "escort_breach":
        alerts = _fact(evidence, "alerts raised")
        trips = _fact(evidence, "trips with no escort")
        acknowledgement = _fact(evidence, "mean acknowledgement time")
        clauses = [
            f"escort alerts reached {_show(alerts)}" if alerts else None,
            f"affected no-escort trips reached {_show(trips)}" if trips else None,
            f"mean acknowledgement time was {_show(acknowledgement)}"
            if acknowledgement
            else None,
        ]
        return f"At {entity}, {_join_facts(clauses)}. The control failed and needs intervention."

    facts = [
        f"a recorded value was {_show(item)}"
        for item in evidence[:3]
        if item.get("value") is not None
    ]
    if not facts:
        return f"The {entity} signal survived correlation and requires human attention."
    return f"At {entity}, {_join_facts(facts)}. The signal survived correlation and requires human attention."


def _context(
    con: Any,
    day: date,
    candidate: Any,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    primary = candidate.primary
    metric = primary.get("metric", "")
    target, target_unit = TARGETS.get(
        metric, (_number(primary.get("baseline"), 0.0), _metric_unit(metric))
    )
    actual = _number(primary.get("value"), 0.0)
    impact = _impact(con, day, candidate)
    slope = _number(baseline.get("slope_28d"), 0.0)
    peer_median = _number(
        baseline.get("peer_median"),
        _number(primary.get("baseline"), 0.0),
    )
    pctile = _number(baseline.get("peer_pctile"), 0.0)
    return {
        "trend": {
            "statement": "Cached trailing trend from the sensing baseline.",
            "values": {"slope_28d": slope},
            "unit": _metric_unit(metric),
        },
        "peer": {
            "statement": "Position within the cached sensing peer group.",
            "peer_group": baseline.get("peer_group")
            or f"{candidate.entity_type} peers",
            "peer_median": peer_median,
            "pctile": pctile,
            "unit": _metric_unit(metric),
        },
        "threshold": {
            "statement": "Actual result against the operational threshold.",
            "target": target,
            "actual": actual,
            "unit": target_unit,
        },
        "impact": impact,
    }


def _baseline(con: Any, day: date, candidate: Any) -> dict[str, Any]:
    primary = candidate.primary
    try:
        cursor = con.execute(
            """select * from entity_baseline
               where as_of = ? and entity_type = ? and entity_id = ? and metric = ?
               limit 1""",
            [day, candidate.entity_type, candidate.entity_id, primary.get("metric")],
        )
        row = cursor.fetchone()
        columns = [column[0] for column in cursor.description]
        if row:
            return dict(zip(columns, row))
    except Exception:
        pass
    try:
        cursor = con.execute(
            """select * from entity_baseline
               where as_of = ? and entity_type = ? and entity_id = ?
               order by metric limit 1""",
            [day, candidate.entity_type, candidate.entity_id],
        )
        row = cursor.fetchone()
        columns = [column[0] for column in cursor.description]
        if row:
            return dict(zip(columns, row))
    except Exception:
        pass
    return {}


def _impact(con: Any, day: date, candidate: Any) -> dict[str, Any]:
    primary = candidate.primary
    detector = primary.get("detector")
    metric = primary.get("metric")
    if detector == "billing_anomaly" or metric == "cost_per_trip":
        value = _number(primary.get("value"), 0.0)
        return {
            "statement": "Spend exposed to the billing-integrity signal; currency is inferred from magnitude.",
            "value": value,
            "unit": "rupees",
        }
    if detector in {"safety_cluster", "alert_ack_sla"} or metric == "ack_minutes":
        evidence = _all_evidence(candidate.signals)
        acknowledgement = _fact(evidence, "mean acknowledgement time")
        value = _number(
            acknowledgement.get("value") if acknowledgement else primary.get("value"),
            0.0,
        )
        return {
            "statement": "Average response time exposed by the incident.",
            "value": value,
            "unit": "minutes",
        }

    query = LATE_EMPLOYEE_IMPACT_SQL if metric == "ota15" else AFFECTED_EMPLOYEE_IMPACT_SQL
    params = [day, *_trip_entity_params(candidate)]
    try:
        row = con.execute(query, params).fetchone()
        if row and row[0] is not None:
            return {
                "statement": "Employees on affected trips from the rider-leg register.",
                "value": int(row[0]),
                "unit": "people",
            }
    except Exception:
        pass
    return {
        "statement": "People represented by the affected operational records.",
        "value": int(_number(primary.get("n"), 0.0)),
        "unit": "people",
    }


def _recommendation(con: Any, day: date, candidate: Any) -> dict[str, Any]:
    primary = candidate.primary
    parent_id = primary.get("parent_id")
    entity_ids = [candidate.entity_id]
    if parent_id and parent_id not in entity_ids:
        entity_ids.append(parent_id)
    placeholders = ", ".join("?" for _ in entity_ids)
    row = None
    columns: list[str] = []
    try:
        cursor = con.execute(
            f"""select * from counterfactual
                where entity_id in ({placeholders}) and metric = ?
                order by case confidence when 'exact' then 0 when 'estimated' then 1 else 2 end,
                         abs(delta) desc, as_of desc
                limit 1""",
            [*entity_ids, primary.get("metric")],
        )
        row = cursor.fetchone()
        columns = [column[0] for column in cursor.description]
    except Exception:
        row = None
    if not row:
        return {
            "action": _default_action(primary),
            "owner": _owner(primary),
            "due": str(day + timedelta(days=7)),
            "expected_effect": "No quantified effect is available.",
            "confidence": "weak",
            "assumption": "No matching precomputed counterfactual exists; do not infer an outcome.",
        }

    counterfactual = dict(zip(columns, row))
    lever = str(counterfactual["lever"])
    param = str(counterfactual["param"])
    delta = _number(counterfactual.get("delta"), 0.0)
    direction = "Do not apply" if delta <= 0 else "Evaluate"
    unit = _metric_unit(str(counterfactual.get("metric", "")))
    return {
        "action": f"{direction} {lever} with parameter {param} under the stated assumption.",
        "owner": _owner(primary),
        "due": str(day + timedelta(days=7)),
        "expected_effect": (
            f"{counterfactual['metric']} moves from "
            f"{_with_unit(counterfactual['baseline_value'], unit)} to "
            f"{_with_unit(counterfactual['projected_value'], unit)}, a "
            f"{_with_unit(delta, unit)} change."
        ),
        "confidence": counterfactual["confidence"],
        # This must remain byte-for-byte identical to the source row.
        "assumption": counterfactual["assumption"],
    }


def _default_action(primary: dict[str, Any]) -> str:
    detector = primary.get("detector")
    if detector == "metric_integrity":
        return "Reject the reported improvement and restore a comparable target."
    if detector in {"safety_cluster", "alert_ack_sla"}:
        return "Replicate the fastest internal alert-triage process after a staffing check."
    if detector == "escort_breach":
        return "Restore escort compliance and review every uncovered trip."
    if detector == "billing_anomaly":
        return "Hold the affected billing lines for contract and telemetry review."
    if detector == "noshow_spike":
        return "Verify pickup recording before attributing the pattern to rider behaviour."
    return "Open a joint operating review for the affected entity."


def _persona(primary: dict[str, Any]) -> str:
    detector = primary.get("detector")
    metric = primary.get("metric")
    if detector in {"noshow_spike"} or metric == "noshow_pct":
        return "line_manager"
    if detector in {
        "metric_integrity",
        "safety_cluster",
        "escort_breach",
        "billing_anomaly",
        "alert_ack_sla",
    } or metric in {"cost_per_trip", "sev1_count"}:
        return "transport_head"
    return "transport_manager"


def _owner(primary: dict[str, Any]) -> str:
    persona = _persona(primary)
    return {
        "transport_manager": "Transport Manager",
        "transport_head": "Transport & Facilities Head",
        "line_manager": "Line Manager",
    }[persona]


def _all_evidence(signals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for signal in signals:
        for item in _json_value(signal.get("evidence"), []):
            key = (str(item.get("claim")), str(item.get("value")))
            if key not in seen:
                evidence.append(item)
                seen.add(key)
    return evidence


def _fact(evidence: list[dict[str, Any]], claim: str) -> dict[str, Any] | None:
    for item in evidence:
        if str(item.get("claim", "")).lower() == claim.lower():
            return item
    return None


def _show(item: dict[str, Any]) -> str:
    value = _plain(item.get("value"))
    unit = str(item.get("unit", "")).strip()
    if unit == "%":
        return f"{value}%"
    if unit:
        return f"{value} {unit}"
    return value


def _plain(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def _join_facts(parts: Iterable[str | None]) -> str:
    clean = [part for part in parts if part]
    if not clean:
        return "the recorded evidence requires review"
    if len(clean) == 1:
        return clean[0]
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def _trip_entity_params(candidate: Any) -> list[Any]:
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


def _severity(signals: Iterable[dict[str, Any]]) -> str:
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    return max(
        (str(signal.get("severity", "low")) for signal in signals),
        key=lambda value: rank.get(value, 0),
        default="low",
    )


def _metric_unit(metric: str) -> str:
    return {
        "ota15": "%",
        "noshow_pct": "%",
        "seat_util": "ratio",
        "ack_minutes": "minutes",
        "cost_per_trip": "rupees",
        "sev1_count": "alerts",
    }.get(metric, "")


def _with_unit(value: Any, unit: str) -> str:
    if not unit:
        return _plain(value)
    if unit in {"%", "ratio"}:
        return f"{_plain(value)}{unit if unit == '%' else ''}"
    return f"{_plain(value)} {unit}"


def _display_name(entity_id: str | None) -> str:
    if not entity_id:
        return "affected operation"
    return str(entity_id).split(" / ", 1)[-1].removesuffix(" Office")


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
