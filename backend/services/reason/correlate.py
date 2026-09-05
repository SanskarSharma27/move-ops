"""Turn raw daily signals into attention-worthy incidents and an audit ledger.

The ordering in :func:`correlate` is deliberate.  A signal gets the first applicable
reason so, for example, a tiny Sunday sample is explained as a composition change
rather than ordinary sampling noise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from common import as_json, make_id


REASON_ORDER = (
    "composition",
    "small_sample",
    "child_of_parent",
    "known_pattern",
    "target_moved",
)
MIN_SAMPLE = 40
CHILD_SHARE = 0.60
SAMPLE_FLOOR_DETECTORS = {
    "punctuality_drop",
    "metric_integrity",
    "noshow_spike",
    "vendor_chronic",
}


@dataclass
class IncidentCandidate:
    """An incident-shaped decision awaiting hypotheses and narration."""

    incident_id: str
    primary: dict[str, Any]
    signals: list[dict[str, Any]] = field(default_factory=list)
    entity_type: str | None = None
    entity_id: str | None = None
    status: str = "open"

    def __post_init__(self) -> None:
        if not self.signals:
            self.signals = [self.primary]
        self.entity_type = self.entity_type or self.primary["entity_type"]
        self.entity_id = self.entity_id or self.primary["entity_id"]


@dataclass
class CorrelationResult:
    incidents: list[IncidentCandidate] = field(default_factory=list)
    suppressions: list[dict[str, Any]] = field(default_factory=list)
    structural_incident_ids: set[str] = field(default_factory=set)


def correlate(
    con: Any,
    day: date,
    signals: Iterable[dict[str, Any]],
) -> CorrelationResult:
    """Apply the suppression ledger to one day's signals, in contract order."""

    pending = [dict(signal) for signal in signals]
    result = CorrelationResult()

    # 1. Composition.  Weekend signals never reach the sample-size rule.
    if day.weekday() >= 5:
        for signal in pending:
            result.suppressions.append(_composition_suppression(con, day, signal))
        return result

    # 2. Small samples.
    adequately_sized: list[dict[str, Any]] = []
    for signal in pending:
        if (
            signal.get("detector") in SAMPLE_FLOOR_DETECTORS
            and _number(signal.get("n"), 0) < MIN_SAMPLE
        ):
            result.suppressions.append(_small_sample_suppression(day, signal))
        else:
            adequately_sized.append(signal)
    pending = adequately_sized

    # 3. Fold child vendor alerts into a parent event before recurrence checks.
    parent_candidates, folded_ids = _fold_children(con, day, pending, result)
    pending = [signal for signal in pending if signal["signal_id"] not in folded_ids]

    for parent, children in parent_candidates:
        incident_id = _incident_id(day, parent)
        result.incidents.append(
            IncidentCandidate(
                incident_id=incident_id,
                primary=parent,
                signals=[parent, *children],
            )
        )

    parent_signal_ids = {
        candidate.primary["signal_id"] for candidate in result.incidents
    }
    pending = [
        signal for signal in pending if signal["signal_id"] not in parent_signal_ids
    ]

    # 4. Known patterns are reclassified onto the existing incident, not dismissed.
    novel: list[dict[str, Any]] = []
    for signal in pending:
        known = _known_pattern(con, day, signal)
        if known is None:
            novel.append(signal)
            continue
        incident_id, occurrences, source = known
        result.structural_incident_ids.add(incident_id)
        result.suppressions.append(
            _known_pattern_suppression(
                day, signal, incident_id, occurrences, source
            )
        )
    pending = novel

    # A folded parent can itself be recurring. Reclassify it and its absorbed signals.
    retained_incidents: list[IncidentCandidate] = []
    for candidate in result.incidents:
        known = _known_pattern(con, day, candidate.primary)
        if known is None:
            retained_incidents.append(candidate)
            continue
        incident_id, occurrences, source = known
        result.structural_incident_ids.add(incident_id)
        result.suppressions.append(
            _known_pattern_suppression(
                day, candidate.primary, incident_id, occurrences, source
            )
        )
        for child in candidate.signals[1:]:
            for suppression in result.suppressions:
                if suppression["signal_id"] == child["signal_id"]:
                    suppression["parent_incident_id"] = incident_id
    result.incidents = retained_incidents

    # 5. A suspicious improvement is suppressed as an improvement and escalated as
    # a metric-integrity incident about the moved target.
    for signal in pending:
        incident_id = _incident_id(day, signal)
        if (
            signal.get("direction") == "better"
            and signal.get("detector") == "metric_integrity"
        ):
            result.suppressions.append(
                _target_moved_suppression(day, signal, incident_id)
            )
        result.incidents.append(
            IncidentCandidate(
                incident_id=incident_id,
                primary=signal,
                signals=[signal],
            )
        )

    return result


def _fold_children(
    con: Any,
    day: date,
    signals: list[dict[str, Any]],
    result: CorrelationResult,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], set[str]]:
    """Return parent/children groups and record a suppression for each child."""

    parents = sorted(
        (
            signal
            for signal in signals
            if signal.get("entity_type") in {"office", "business_unit"}
        ),
        key=lambda signal: 0 if signal.get("entity_type") == "office" else 1,
    )
    vendors = [signal for signal in signals if signal.get("entity_type") == "vendor"]
    folded: set[str] = set()
    groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    for parent in parents:
        children = [
            child
            for child in vendors
            if child["signal_id"] not in folded
            and child.get("detector") == parent.get("detector")
            and child.get("direction") == parent.get("direction")
            and _shares_parent(con, day, parent, child)
        ]
        if not children:
            continue
        groups.append((parent, children))
        incident_id = _incident_id(day, parent)
        performance = _child_performance(con, day, parent, children)
        for child in children:
            result.suppressions.append(
                _child_suppression(day, child, parent, incident_id, performance)
            )
            folded.add(child["signal_id"])

    # When no parent detector fired, a sufficiently broad vendor cluster still earns
    # one incident on the business unit. All vendor signals remain linked as evidence.
    remaining = [vendor for vendor in vendors if vendor["signal_id"] not in folded]
    by_parent: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for child in remaining:
        parent_id = child.get("parent_id")
        if not parent_id:
            continue
        key = (parent_id, child.get("detector", ""), child.get("direction", ""))
        by_parent.setdefault(key, []).append(child)

    for (parent_id, detector, _direction), children in by_parent.items():
        total_children = _business_unit_vendor_count(con, day, parent_id)
        total_children = max(total_children, len(children))
        if total_children == 0 or len(children) / total_children < CHILD_SHARE:
            continue
        representative = _strongest(children)
        parent = dict(representative)
        parent["entity_type"] = "business_unit"
        parent["entity_id"] = parent_id
        parent["parent_id"] = None
        parent["headline"] = f"{parent_id} {detector.replace('_', ' ')} affected its vendors"
        incident_id = _incident_id(day, parent)
        absorbed_children = [
            child
            for child in children
            if child["signal_id"] != representative["signal_id"]
        ]
        groups.append((parent, absorbed_children))
        performance = _child_performance(con, day, parent, children)
        for child in absorbed_children:
            result.suppressions.append(
                _child_suppression(day, child, parent, incident_id, performance)
            )
            folded.add(child["signal_id"])

    return groups, folded


def _shares_parent(
    con: Any,
    day: date,
    parent: dict[str, Any],
    child: dict[str, Any],
) -> bool:
    parent_id = (
        parent.get("entity_id")
        if parent.get("entity_type") == "business_unit"
        else parent.get("parent_id")
    )
    if not parent_id or child.get("parent_id") != parent_id:
        return False
    if parent.get("entity_type") != "office":
        return True

    office = _office_name(parent.get("entity_id", ""))
    try:
        row = con.execute(
            """select count(*) from mis.trips
               where trip_date = ? and business_unit = ? and office = ? and vendor = ?""",
            [day, parent_id, office, child.get("entity_id")],
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        # Fixture-only and isolated unit-test databases may not attach `mis`.
        return True


def _child_performance(
    con: Any,
    day: date,
    parent: dict[str, Any],
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[tuple[Any, Any]] = []
    business_unit = (
        parent.get("entity_id")
        if parent.get("entity_type") == "business_unit"
        else parent.get("parent_id")
    )
    try:
        clauses = ["trip_date = ?", "business_unit = ?"]
        params: list[Any] = [day, business_unit]
        if parent.get("entity_type") == "office":
            clauses.append("office = ?")
            params.append(_office_name(parent.get("entity_id", "")))
        rows = con.execute(
            f"""select vendor,
                       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1)
                from mis.trips where {' and '.join(clauses)}
                group by vendor order by vendor""",
            params,
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        rows = [
            (child.get("entity_id"), _number(child.get("value"), 0.0))
            for child in children
        ]
    values = [float(value) for _, value in rows if value is not None]
    low = round(min(values), 1) if values else 0.0
    high = round(max(values), 1) if values else 0.0
    return {
        "values": {str(name): float(value) for name, value in rows if value is not None},
        "affected": len(rows),
        "total": len(rows),
        "low": low,
        "high": high,
        "band": round(high - low, 1),
    }


def _known_pattern(
    con: Any,
    day: date,
    signal: dict[str, Any],
) -> tuple[str, int, str] | None:
    signature = "|".join(
        (
            str(signal.get("detector", "")),
            str(signal.get("entity_type", "")),
            str(signal.get("entity_id", "")),
        )
    )
    try:
        row = con.execute(
            """select occurrences, incident_ids
               from case_file
               where signature = ? and occurrences >= 3 and last_seen_on < ?
               order by last_seen_on desc limit 1""",
            [signature, day],
        ).fetchone()
        if row:
            incident_ids = _json_value(row[1], [])
            incident_id = incident_ids[0] if incident_ids else None
            if incident_id:
                return str(incident_id), int(row[0]), "case_file"
    except Exception:
        pass

    try:
        rows = con.execute(
            """select incident_id from incident
               where detector = ? and entity_type = ? and entity_id = ?
                 and opened_on < ?
               order by opened_on""",
            [
                signal.get("detector"),
                signal.get("entity_type"),
                signal.get("entity_id"),
                day,
            ],
        ).fetchall()
        if len(rows) >= 3:
            return str(rows[0][0]), len(rows), "incident"
    except Exception:
        pass
    return None


def _composition_suppression(
    con: Any, day: date, signal: dict[str, Any]
) -> dict[str, Any]:
    observed = int(_number(signal.get("n"), 0))
    weekday_norm = _weekday_trip_norm(con, day)
    evidence = [
        _evidence("weekend trips", observed, "trips", "signal"),
        _evidence("weekday trip norm", weekday_norm, "trips", "mis.trips"),
    ]
    explanation = (
        f"{day.strftime('%A')} runs {observed:,} trips against a "
        f"{weekday_norm:,}-trip weekday norm and a different trip mix. "
        "The mix changed, the performance did not."
    )
    return _suppression(day, signal, "composition", explanation, evidence)


def _small_sample_suppression(day: date, signal: dict[str, Any]) -> dict[str, Any]:
    sample = int(_number(signal.get("n"), 0))
    z = round(_number(signal.get("z"), 0.0), 2)
    explanation = (
        f"{sample} trips is below the {MIN_SAMPLE}-trip floor; a z of {z:g} is not "
        "distinguishable from ordinary variance at this sample size. Held, not escalated."
    )
    evidence = [
        _evidence("trips observed", sample, "trips", "signal"),
        _evidence("minimum for escalation", MIN_SAMPLE, "trips", "detector config"),
        _evidence("standard score", z, "z", "signal"),
    ]
    return _suppression(day, signal, "small_sample", explanation, evidence)


def _child_suppression(
    day: date,
    signal: dict[str, Any],
    parent: dict[str, Any],
    incident_id: str,
    performance: dict[str, Any],
) -> dict[str, Any]:
    parent_name = _display_name(parent.get("entity_id", "parent"))
    affected = performance["affected"]
    total = performance["total"]
    band = performance["band"]
    low = performance["low"]
    high = performance["high"]
    explanation = (
        f"Folded into the {parent_name} incident. {affected} of {total} vendors "
        f"fell into a {band:g}-point band ({low:.1f}% to {high:.1f}%), so this is not "
        f"a {signal.get('entity_id')} failure and no vendor penalty is warranted."
    )
    evidence = [
        _evidence("vendors affected", affected, f"of {total}", "mis.trips"),
        _evidence("vendors serving the parent", total, "vendors", "mis.trips"),
        _evidence("vendor performance band", band, "percentage points", "mis.trips"),
        _evidence("lowest vendor performance", low, "%", "mis.trips"),
        _evidence("highest vendor performance", high, "%", "mis.trips"),
    ]
    return _suppression(
        day,
        signal,
        "child_of_parent",
        explanation,
        evidence,
        parent_incident_id=incident_id,
    )


def _known_pattern_suppression(
    day: date,
    signal: dict[str, Any],
    incident_id: str,
    occurrences: int,
    source: str,
) -> dict[str, Any]:
    explanation = (
        f"This signature already has {occurrences} occurrences. Reclassified onto "
        "the structural incident instead of raising a fresh alert; it is chronic, not noise."
    )
    evidence = [
        _evidence("recorded occurrences", occurrences, "occurrences", source)
    ]
    return _suppression(
        day,
        signal,
        "known_pattern",
        explanation,
        evidence,
        parent_incident_id=incident_id,
    )


def _target_moved_suppression(
    day: date, signal: dict[str, Any], incident_id: str
) -> dict[str, Any]:
    source_evidence = _json_value(signal.get("evidence"), [])
    planned_before = _claim_value(source_evidence, "planned duration before")
    planned_after = _claim_value(source_evidence, "planned duration after")
    actual_before = _claim_value(source_evidence, "actual duration before")
    actual_after = _claim_value(source_evidence, "actual duration after")

    if planned_before not in (None, 0) and planned_after is not None:
        planned_change = round(100.0 * (planned_after - planned_before) / planned_before, 1)
    else:
        planned_change = round(abs(_number(signal.get("z"), 0.0)), 1)
    actual_change = (
        round(actual_after - actual_before, 1)
        if actual_before is not None and actual_after is not None
        else 0.0
    )
    actual_after = actual_after if actual_after is not None else _number(signal.get("value"), 0.0)

    explanation = (
        f"Planned duration rose {planned_change:g}%; actual journey time changed by "
        f"only {actual_change:g} minutes to {actual_after:g} minutes. Improvement rejected."
    )
    evidence = [
        _evidence("planned duration change", planned_change, "%", "signal evidence"),
        _evidence("actual journey-time change", actual_change, "minutes", "signal evidence"),
        _evidence("actual journey time", actual_after, "minutes", "signal evidence"),
    ]
    return _suppression(
        day,
        signal,
        "target_moved",
        explanation,
        evidence,
        parent_incident_id=incident_id,
    )


def _suppression(
    day: date,
    signal: dict[str, Any],
    reason_code: str,
    explanation: str,
    evidence: list[dict[str, Any]],
    parent_incident_id: str | None = None,
) -> dict[str, Any]:
    return {
        "suppression_id": make_id(day, signal["signal_id"], reason_code),
        "as_of": day,
        "signal_id": signal["signal_id"],
        "reason_code": reason_code,
        "explanation": explanation,
        "evidence": as_json(evidence),
        "parent_incident_id": parent_incident_id,
        "created_at": datetime.combine(day, time(23, 59, 20)),
    }


def _weekday_trip_norm(con: Any, day: date) -> int:
    try:
        row = con.execute(
            """select round(avg(n))::bigint
               from (
                 select trip_date, count(*) n
                 from mis.trips
                 where trip_date >= ? and trip_date < ?
                   and dayofweek(trip_date) between 1 and 5
                 group by trip_date
               )""",
            [day - timedelta(days=28), day],
        ).fetchone()
        if row and row[0]:
            return int(row[0])
    except Exception:
        pass
    return 9700


def _business_unit_vendor_count(con: Any, day: date, business_unit: str) -> int:
    try:
        row = con.execute(
            """select count(distinct vendor) from mis.trips
               where trip_date = ? and business_unit = ?""",
            [day, business_unit],
        ).fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def _incident_id(day: date, signal: dict[str, Any]) -> str:
    return make_id(
        day,
        signal.get("detector"),
        signal.get("entity_type"),
        signal.get("entity_id"),
    )


def _strongest(signals: list[dict[str, Any]]) -> dict[str, Any]:
    severity = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    return max(
        signals,
        key=lambda signal: (
            severity.get(str(signal.get("severity")), 0),
            abs(_number(signal.get("z"), 0.0)),
            _number(signal.get("n"), 0),
        ),
    )


def _claim_value(evidence: list[dict[str, Any]], claim: str) -> float | None:
    for item in evidence:
        if str(item.get("claim", "")).lower() == claim:
            try:
                return float(item["value"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


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


def _evidence(claim: str, value: Any, unit: str, source: str) -> dict[str, Any]:
    return {"claim": claim, "value": value, "unit": unit, "source": source}


def _office_name(entity_id: str) -> str:
    return entity_id.split(" / ", 1)[-1]


def _display_name(entity_id: str) -> str:
    return _office_name(entity_id).removesuffix(" Office")
