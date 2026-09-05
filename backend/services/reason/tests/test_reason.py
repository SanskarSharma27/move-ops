from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from importlib import import_module
from pathlib import Path

import duckdb
import pytest


BACKEND = Path(__file__).resolve().parents[3]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from common import as_json, upsert  # noqa: E402
from services.reason.correlate import correlate  # noqa: E402
from services.reason.hypotheses import (  # noqa: E402
    CAPACITY_SHORTFALL_SQL,
    COMPOSITION_CONTROL_SQL,
    COMPOSITION_SHIFT_SQL,
    DEMAND_SURGE_SQL,
    SCHEDULE_LAG_SQL,
    SYSTEMIC_DAY_EVENT_SQL,
    SYSTEMIC_SIGNAL_FALLBACK_SQL,
    VENDOR_FAILURE_SQL,
)
from services.reason.narrate import build_incident  # noqa: E402
from services.reason.pipeline import run_day  # noqa: E402
from services.reason.router import WhatIfRequest  # noqa: E402


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute((ROOT / "contracts" / "schema.sql").read_text())
    connection.execute("attach ':memory:' as mis")
    connection.execute(
        """create table mis.trips (
             trip_date date, business_unit varchar, office varchar, vendor varchar,
             shift_type varchar, trip_id bigint, is_ontime_15 boolean,
             actual_cab_registration varchar, actual_start bigint, actual_end bigint,
             planned_start bigint, planned_end bigint, traveled_km double,
             planned_km double, fuel_type varchar
           )"""
    )
    connection.execute("create table mis.emp_legs (trip_id bigint)")
    yield connection
    connection.close()


def test_five_vendor_signals_become_one_parent_incident_and_four_suppressions(con):
    day = date(2026, 7, 21)
    signals = [
        _signal(
            f"vendor-{index}",
            day,
            entity_type="vendor",
            entity_id=f"Vendor {index}",
            parent_id="vanta-Aus",
            value=58.0 + index,
            n=100,
        )
        for index in range(5)
    ]

    decisions = correlate(con, day, signals)

    assert len(decisions.incidents) == 1
    assert decisions.incidents[0].entity_type == "business_unit"
    assert decisions.incidents[0].entity_id == "vanta-Aus"
    assert len(decisions.suppressions) == 4
    assert {row["reason_code"] for row in decisions.suppressions} == {
        "child_of_parent"
    }
    assert all(row["parent_incident_id"] for row in decisions.suppressions)
    assert all(
        "no vendor penalty is warranted" in row["explanation"]
        for row in decisions.suppressions
    )


def test_sunday_signal_is_composition_before_small_sample(con):
    day = date(2026, 7, 19)
    signal = _signal("sunday", day, n=12)

    decisions = correlate(con, day, [signal])

    assert not decisions.incidents
    assert len(decisions.suppressions) == 1
    assert decisions.suppressions[0]["reason_code"] == "composition"
    assert "12" in decisions.suppressions[0]["explanation"]


def test_parent_signal_folds_canonical_vendor_children(con):
    day = date(2026, 7, 21)
    vendors = ["Sneha", "Meera", "Priya", "Anjali", "Sanjay"]
    con.executemany(
        """insert into mis.trips values
           (?, 'vanta-Aus', 'Cedar Ridge Office', ?, '09:00', ?, false,
            ?, 0, 60, 0, 60, 20, 20, 'Diesel')""",
        [
            [day, vendor, index, f"cab-{index}"]
            for index, vendor in enumerate(vendors, 1)
        ],
    )
    parent = _signal("parent", day)
    children = [
        _signal(
            vendor.lower(),
            day,
            entity_type="vendor",
            entity_id=vendor,
            parent_id="vanta-Aus",
            value=60.0,
            n=100,
        )
        for vendor in vendors[:2]
    ]

    decisions = correlate(con, day, [parent, *children])

    assert len(decisions.incidents) == 1
    assert len(decisions.suppressions) == 2
    assert len(decisions.incidents[0].signals) == 3
    assert all(
        row["parent_incident_id"] == decisions.incidents[0].incident_id
        for row in decisions.suppressions
    )


def test_small_sample_is_held(con):
    day = date(2026, 7, 10)
    signal = _signal("small", day, n=33, z=-2.02)

    decisions = correlate(con, day, [signal])

    assert not decisions.incidents
    assert len(decisions.suppressions) == 1
    assert decisions.suppressions[0]["reason_code"] == "small_sample"
    assert "33" in decisions.suppressions[0]["explanation"]
    assert "40" in decisions.suppressions[0]["explanation"]


def test_count_based_safety_cluster_survives_with_twenty_three_alerts(con):
    day = date(2026, 7, 15)
    signal = _signal(
        "safety",
        day,
        entity_type="business_unit",
        entity_id="catalyst-Sac",
        parent_id=None,
        detector="safety_cluster",
        value=23,
        n=23,
    )

    decisions = correlate(con, day, [signal])

    assert len(decisions.incidents) == 1
    assert not decisions.suppressions


def test_known_pattern_is_reclassified_not_reopened(con):
    day = date(2026, 7, 29)
    signal = _signal("recurrence", day)
    upsert(
        con,
        "case_file",
        [
            {
                "case_id": "case-1",
                "signature": (
                    "punctuality_drop|office|vanta-Aus / Cedar Ridge Office"
                ),
                "entity_type": "office",
                "entity_id": "vanta-Aus / Cedar Ridge Office",
                "opened_on": date(2026, 7, 8),
                "last_seen_on": date(2026, 7, 28),
                "occurrences": 3,
                "status": "recurring",
                "incident_ids": as_json(["prior-incident"]),
                "diagnosis": "Recurring capacity mismatch.",
                "created_at": datetime(2026, 7, 8, 23, 59),
                "updated_at": datetime(2026, 7, 28, 23, 59),
            }
        ],
        key="case_id",
    )

    decisions = correlate(con, day, [signal])

    assert not decisions.incidents
    assert decisions.structural_incident_ids == {"prior-incident"}
    assert decisions.suppressions[0]["reason_code"] == "known_pattern"
    assert "chronic, not noise" in decisions.suppressions[0]["explanation"]


def test_better_metric_integrity_signal_rejects_the_improvement(con):
    day = date(2026, 7, 20)
    signal = _integrity_signal(day)

    decisions = correlate(con, day, [signal])

    assert len(decisions.incidents) == 1
    assert decisions.incidents[0].primary["detector"] == "metric_integrity"
    assert len(decisions.suppressions) == 1
    suppression = decisions.suppressions[0]
    assert suppression["reason_code"] == "target_moved"
    assert suppression["parent_incident_id"] == decisions.incidents[0].incident_id
    assert "Improvement rejected" in suppression["explanation"]


def test_every_incident_has_complete_context(con):
    day = date(2026, 7, 20)
    _put_signals(con, [_integrity_signal(day)])

    run_day(con, day)

    contexts = [
        json.loads(row[0])
        for row in con.execute("select context from incident").fetchall()
    ]
    assert contexts
    for context in contexts:
        assert set(context) == {"trend", "peer", "threshold", "impact"}
        assert isinstance(context["impact"]["value"], (int, float))
        assert context["impact"]["unit"] in {"rupees", "minutes", "people"}


def test_every_narrative_number_is_absorbed_signal_evidence(con):
    day = date(2026, 7, 20)
    signal = _integrity_signal(day)
    candidate = correlate(con, day, [signal]).incidents[0]
    hypotheses = [
        {"name": "systemic_day_event", "verdict": "refuted"},
        {"name": "schedule_lag", "verdict": "supported"},
    ]

    incident = build_incident(con, candidate, day, hypotheses)

    sourced = {
        float(item["value"])
        for absorbed in candidate.signals
        for item in json.loads(absorbed["evidence"])
        if isinstance(item.get("value"), (int, float))
    }
    numbers = {
        float(token.replace(",", ""))
        for token in re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", incident["narrative"])
    }
    assert numbers
    assert numbers <= sourced


def test_running_the_same_day_twice_keeps_row_counts_identical(con):
    day = date(2026, 7, 20)
    _put_signals(con, [_integrity_signal(day)])

    run_day(con, day)
    first = _reason_counts(con)
    run_day(con, day)
    second = _reason_counts(con)

    assert first == second
    assert first["incident"] == 1
    assert first["suppression"] == 1
    assert first["hypothesis"] >= 2


def test_fixed_hypothesis_queries_accept_their_bound_parameters(con):
    day = date(2026, 7, 21)
    start = date(2026, 6, 23)
    entity = ["office", "vanta-Aus / Cedar Ridge Office"] * 4
    executions = [
        (
            VENDOR_FAILURE_SQL,
            [
                day,
                "office",
                "vanta-Aus / Cedar Ridge Office",
                "office",
                "vanta-Aus / Cedar Ridge Office",
                "office",
                "vanta-Aus",
            ],
        ),
        (
            SYSTEMIC_DAY_EVENT_SQL,
            [day, "ota15", "office", "vanta-Aus / Cedar Ridge Office"],
        ),
        (
            SYSTEMIC_SIGNAL_FALLBACK_SQL,
            [day, "punctuality_drop", "office", "vanta-Aus / Cedar Ridge Office"],
        ),
        (DEMAND_SURGE_SQL, [start, day, *entity, day, day, day]),
        (
            CAPACITY_SHORTFALL_SQL,
            [start, day, *entity, day, day, day, day, day, day],
        ),
        (SCHEDULE_LAG_SQL, [date(2026, 5, 1), day, *entity]),
        (COMPOSITION_SHIFT_SQL, [date(2026, 5, 1), day, *entity]),
        (COMPOSITION_CONTROL_SQL, [date(2026, 5, 1), day]),
    ]

    for sql, params in executions:
        con.execute(sql, params).fetchall()


def test_bad_whatif_returns_a_considered_negative_answer(con, monkeypatch):
    day = date(2026, 7, 20)
    _put_signals(con, [_integrity_signal(day)])
    run_day(con, day)
    incident_id = con.execute("select incident_id from incident").fetchone()[0]
    upsert(
        con,
        "counterfactual",
        [
            {
                "as_of": date(2026, 7, 31),
                "entity_type": "office",
                "entity_id": "catalyst-Sac / Santa Clara Office",
                "lever": "vendor_substitute",
                "param": "Rohan Mikhailov Travel",
                "metric": "ota15",
                "baseline_value": 47.8,
                "projected_value": 47.2,
                "delta": -0.6,
                "n": 13091,
                "assumption": "Applies the observed replacement rate to the incumbent trips.",
                "confidence": "estimated",
            }
        ],
        key=("as_of", "entity_type", "entity_id", "lever", "param", "metric"),
    )
    reason_router = import_module("services.reason.router")
    monkeypatch.setattr(reason_router, "db", lambda read_only=False: _NoClose(con))

    response = reason_router.whatif(
        WhatIfRequest(
            incident_id=incident_id,
            lever="vendor_substitute",
            param="Rohan Mikhailov Travel",
        )
    )

    assert response["delta"] == -0.6
    assert "worse" in response["narrative"]
    assert "not recommended" in response["narrative"]


def _reason_counts(con) -> dict[str, int]:
    return {
        table: con.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("incident", "suppression", "hypothesis")
    }


class _NoClose:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        return None


def _put_signals(con, signals):
    upsert(con, "signal", signals, key="signal_id")


def _signal(
    signal_id: str,
    day: date,
    *,
    entity_type: str = "office",
    entity_id: str = "vanta-Aus / Cedar Ridge Office",
    parent_id: str | None = "vanta-Aus",
    detector: str = "punctuality_drop",
    value: float = 59.3,
    baseline: float = 88.9,
    z: float = -4.18,
    n: int = 978,
    direction: str = "worse",
    evidence: list[dict] | None = None,
) -> dict:
    evidence = evidence or [
        {"claim": "on-time arrival", "value": value, "unit": "%", "source": "test"},
        {
            "claim": "trailing same-weekday baseline",
            "value": baseline,
            "unit": "%",
            "source": "test",
        },
        {"claim": "trips affected", "value": n, "unit": "trips", "source": "test"},
    ]
    return {
        "signal_id": signal_id,
        "as_of": day,
        "detector": detector,
        "severity": "critical",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "parent_id": parent_id,
        "metric": "ota15",
        "value": value,
        "baseline": baseline,
        "z": z,
        "n": n,
        "direction": direction,
        "headline": "Test signal",
        "evidence": as_json(evidence),
        "created_at": datetime(2026, 7, day.day, 23, 59),
    }


def _integrity_signal(day: date) -> dict:
    evidence = [
        {"claim": "on-time before", "value": 35.8, "unit": "%", "source": "test"},
        {"claim": "on-time after", "value": 52.3, "unit": "%", "source": "test"},
        {
            "claim": "planned duration before",
            "value": 40.6,
            "unit": "minutes",
            "source": "test",
        },
        {
            "claim": "planned duration after",
            "value": 58.1,
            "unit": "minutes",
            "source": "test",
        },
        {
            "claim": "actual duration before",
            "value": 76.4,
            "unit": "minutes",
            "source": "test",
        },
        {
            "claim": "actual duration after",
            "value": 77.0,
            "unit": "minutes",
            "source": "test",
        },
    ]
    return _signal(
        "integrity",
        day,
        entity_id="catalyst-Sac / Santa Clara Office",
        parent_id="catalyst-Sac",
        detector="metric_integrity",
        value=52.3,
        baseline=35.8,
        z=3.9,
        n=5700,
        direction="better",
        evidence=evidence,
    )
