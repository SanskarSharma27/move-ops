from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest


BACKEND = Path(__file__).resolve().parents[3]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from common import as_json, upsert  # noqa: E402
from services.reason.correlate import correlate  # noqa: E402
from services.reason.narrate import build_incident  # noqa: E402
from services.reason.pipeline import run_day  # noqa: E402


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute((ROOT / "contracts" / "schema.sql").read_text())
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


def test_small_sample_is_held(con):
    day = date(2026, 7, 10)
    signal = _signal("small", day, n=33, z=-2.02)

    decisions = correlate(con, day, [signal])

    assert not decisions.incidents
    assert len(decisions.suppressions) == 1
    assert decisions.suppressions[0]["reason_code"] == "small_sample"
    assert "33" in decisions.suppressions[0]["explanation"]
    assert "40" in decisions.suppressions[0]["explanation"]


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


def _reason_counts(con) -> dict[str, int]:
    return {
        table: con.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("incident", "suppression", "hypothesis")
    }


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
