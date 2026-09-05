"""Tests for the sensing layer. Plain pytest, no fixture framework.

Two kinds live here. The synthetic ones exercise detector logic against hand-built
rows and always run. The data ones recompute a known number out of `mis.*` - the
weekday baseline, the schedule-padding step at Santa Clara, vanta-Aus at ten minutes of
padding - and skip themselves with a message if the warehouse has not been built, so a
fresh clone still gets a green `make test`.

There is no `tests/__init__.py` on purpose: it keeps this module importable standalone,
so the sys.path line below runs before anything reaches for `common`.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from common import MIS_DB, make_id  # noqa: E402
from services.sense import baselines, counterfactual, detectors, field_trust  # noqa: E402
from services.sense import pipeline  # noqa: E402

JULY = (date(2026, 7, 1), date(2026, 7, 31))

needs_data = pytest.mark.skipif(
    not MIS_DB.exists(), reason="analytics/mis.duckdb not built - run `make build`")


def _con():
    from common import db
    con = db()
    if not con.execute("select count(*) from field_trust").fetchone()[0]:
        field_trust.audit(con, JULY[0])
    return con


def _base_row(**kw):
    """A minimal entity_baseline row of the shape the detectors read."""
    row = dict(as_of=JULY[0], entity_type="office", entity_id="bu / Site",
               parent_id="bu", metric="ota15", value=90.0, n=500,
               baseline_mean=90.0, baseline_sd=3.0, baseline_n=4, z=0.0,
               peer_group="bu offices", peer_median=90.0, peer_pctile=50.0,
               slope_28d=0.0)
    row.update(kw)
    return row


# ------------------------------------------------------------------ 1. identity

def test_make_id_is_stable_across_calls():
    a = make_id("2026-07-21", "punctuality_drop", "office", "Cedar Ridge")
    b = make_id("2026-07-21", "punctuality_drop", "office", "Cedar Ridge")
    assert a == b and len(a) == 16
    assert a != make_id("2026-07-22", "punctuality_drop", "office", "Cedar Ridge")


def test_signal_id_is_built_from_business_values_only():
    """Two runs a week apart must produce the same id for the same business fact."""
    args = (date(2026, 7, 21), "punctuality_drop", "office",
            "vanta-Aus / Cedar Ridge Office", "ota15")
    assert make_id(*args) == make_id(*args)


# --------------------------------------------------- 2. the weekday-aware proof

def test_a_healthy_sunday_raises_nothing():
    """380 trips at 96% on-time, against a Sunday baseline of 96%. No signal.

    This is the case a naive trailing-28-day detector reports as a triumph every
    Sunday, because it compares a 96% Sunday against a week whose average is dragged
    down by 60% Tuesdays. Same-weekday baselines make it a non-event.
    """
    sunday = _base_row(as_of=date(2026, 7, 5), value=96.0, n=380,
                       baseline_mean=96.0, baseline_sd=2.0, z=0.0)
    assert detectors.punctuality_drop(None, sunday["as_of"], [sunday]) == []


def test_a_real_drop_still_fires():
    """The same detector, one genuinely bad day, so the test above proves something."""
    bad = _base_row(as_of=date(2026, 7, 21), entity_id="vanta-Aus / Cedar Ridge Office",
                    parent_id="vanta-Aus", value=59.0, n=971,
                    baseline_mean=84.1, baseline_sd=6.06, z=-4.14)
    out = detectors.punctuality_drop(None, bad["as_of"], [bad])
    assert len(out) == 1
    signal = out[0]
    assert signal["direction"] == "worse"
    assert signal["severity"] == "critical"
    assert signal["parent_id"] == "vanta-Aus"
    assert "59.0%" in signal["headline"] and "84.1%" in signal["headline"]


def test_a_loud_z_on_a_tiny_sample_is_low_severity():
    """PRD: raise it anyway, but say out loud that it rests on nothing."""
    tiny = _base_row(value=20.0, n=5, baseline_mean=80.0, baseline_sd=15.0, z=-4.0)
    assert detectors.punctuality_drop(None, tiny["as_of"], [tiny])[0]["severity"] == "low"


@needs_data
def test_sunday_barely_appears_in_july_punctuality_signals():
    """The same proof against the real data rather than a constructed row."""
    con = _con()
    rows = con.execute(
        """select dayname(as_of) as dow, count(*) from signal
           where detector = 'punctuality_drop' and as_of between ? and ?
           group by 1""", list(JULY)).fetchall()
    con.close()
    if not rows:
        pytest.skip("no signals yet - run `make replay`")
    by_day = dict(rows)
    # A naive detector raises 38 of its 84 July signals on Sundays. Weekday-aware
    # baselines leave at most one, and it is a small-sample vendor rather than the
    # weekend-composition artifact the naive detector was reporting.
    assert by_day.get("Sunday", 0) <= 1
    assert by_day.get("Sunday", 0) < by_day.get("Tuesday", 0)


# ------------------------------------------------------------ 3. the headline detector

@needs_data
def test_metric_integrity_fires_when_the_plan_moves_and_the_journey_does_not():
    """Santa Clara, 20 July: planned minutes per km jump, actual minutes per km do not.

    The 19th carries a single trip at this site, so nothing can be concluded that day;
    the 20th is the first day the padded schedule has real volume behind it.
    """
    con = _con()
    rows = detectors._schedule_padding(con, date(2026, 7, 20))
    con.close()
    santa = [r for r in rows if "Santa Clara" in r["entity_id"]]
    assert len(santa) == 1, "expected the padding step to be caught at Santa Clara"
    signal = santa[0]
    assert signal["detector"] == "metric_integrity"
    assert signal["direction"] == "better"      # improved, and still a defect
    assert signal["severity"] == "critical"

    ev = {e["claim"]: e["value"] for e in json.loads(signal["evidence"])}
    assert ev["planned minutes per km rose"] > 20.0
    assert ev["actual minutes per km moved"] > -5.0   # the journey did not get quicker
    assert ev["planned km per trip today"] == pytest.approx(
        ev["planned km per trip before"], rel=0.10)  # same routes, not longer ones


@needs_data
def test_metric_integrity_leaves_an_ordinary_good_day_alone():
    """It must not simply fire on every improvement, or it says nothing."""
    con = _con()
    rows = detectors._schedule_padding(con, date(2026, 7, 10))
    con.close()
    assert rows == []


# ------------------------------------------------------------ 4. the counterfactual

@needs_data
def test_schedule_pad_of_ten_minutes_reproduces_vanta_aus():
    """Known good: vanta-Aus July is 81.5% as scheduled, 86.7% at +5, 90.4% at +10."""
    con = _con()
    rows = counterfactual._schedule_pad(con, *JULY)
    con.close()
    grid = {r["param"]: r for r in rows
            if r["entity_id"] == "vanta-Aus" and r["entity_type"] == "business_unit"}
    assert grid["10"]["baseline_value"] == pytest.approx(81.5, abs=0.1)
    assert grid["5"]["projected_value"] == pytest.approx(86.7, abs=0.1)
    assert grid["10"]["projected_value"] == pytest.approx(90.4, abs=0.1)
    assert grid["10"]["n"] == 23584
    assert grid["10"]["confidence"] == "exact"


@needs_data
def test_every_counterfactual_carries_an_honest_assumption():
    """`assumption` is mandatory, and padding must never be recommended bare."""
    con = _con()
    rows = counterfactual._schedule_pad(con, *JULY)
    con.close()
    assert rows
    for r in rows:
        assert r["assumption"].strip()
        assert "moves the metric, not the commute" in r["assumption"]


@needs_data
def test_a_substitution_built_on_too_few_trips_is_marked_weak():
    con = _con()
    rows = counterfactual._vendor_substitute(con, *JULY)
    con.close()
    for r in rows:
        n_claimed = int(re.search(r"ran only (\d+) trips here", r["assumption"]).group(1)) \
            if "ran only" in r["assumption"] else None
        if r["confidence"] == "weak":
            assert n_claimed is not None and n_claimed < counterfactual.SUB_WEAK_BELOW
        else:
            assert r["confidence"] == "estimated"


# ---------------------------------------------------------------- 5. the refusal

@needs_data
def test_requesting_a_quarantined_column_raises():
    con = _con()
    try:
        assert field_trust.is_quarantined(con, "trips", "delay_minutes")
        assert field_trust.is_quarantined(con, "feedback", "marshal_rating")
        assert not field_trust.is_quarantined(con, "trips", "actual_end_epoch")
        with pytest.raises(field_trust.QuarantinedColumnError) as err:
            field_trust.require_usable(con, "trips", "delay_minutes",
                                       detector="punctuality_drop")
        # The refusal has to carry the reason, not just the refusal.
        assert "90.2" in str(err.value)
        with pytest.raises(field_trust.QuarantinedColumnError):
            field_trust.SOURCE_COLUMNS["temp_detector"] = [("feedback", "marshal_rating")]
            try:
                field_trust.guard(con, "temp_detector")
            finally:
                field_trust.SOURCE_COLUMNS.pop("temp_detector")
    finally:
        con.close()


@needs_data
def test_the_audit_writes_at_least_eight_verdicts_and_recomputes_them():
    con = _con()
    field_trust.audit(con, JULY[0])
    rows = con.execute(
        "select table_name, column_name, verdict, evidence from field_trust").fetchall()
    con.close()
    assert len(rows) >= 8
    verdicts = {f"{t}.{c}": v for t, c, v, _ in rows}
    assert verdicts["trips.delay_minutes"] == "quarantined"
    assert verdicts["feedback.marshal_rating"] == "quarantined"
    assert verdicts["emp_legs.signintype"] == "trusted"
    # Every evidence sentence must carry the number that justifies its verdict.
    for _, _, _, evidence in rows:
        assert re.search(r"\d", evidence), evidence


# ------------------------------------------------------------- 6. idempotency

@needs_data
def test_run_day_twice_leaves_row_counts_unchanged():
    con = _con()
    day = date(2026, 7, 21)
    tables = ("field_trust", "entity_baseline", "signal", "counterfactual")

    def counts():
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}

    pipeline.run_day(con, day)
    first = counts()
    first_ids = con.execute(
        "select signal_id from signal where as_of = ? order by 1", [day]).fetchall()
    pipeline.run_day(con, day)
    second = counts()
    second_ids = con.execute(
        "select signal_id from signal where as_of = ? order by 1", [day]).fetchall()
    con.close()
    assert first == second
    assert first_ids == second_ids


# ------------------------------------------------------- faithfulness and shape

@needs_data
def test_every_headline_number_appears_in_its_own_evidence():
    """The faithfulness gate is mechanical, so this check is too."""
    con = _con()
    rows = con.execute("select detector, headline, evidence from signal").fetchall()
    con.close()
    if not rows:
        pytest.skip("no signals yet - run `make replay`")
    unsourced = []
    for detector, headline, evidence in rows:
        values = [e["value"] for e in json.loads(evidence)]
        for token in re.findall(r"-?\d+(?:\.\d+)?", headline):
            x = float(token)
            if not any(abs(x - v) <= max(0.05, abs(v) * 0.005) for v in values):
                unsourced.append((detector, token, headline))
    assert not unsourced, unsourced[:5]


@needs_data
def test_baselines_carry_the_hierarchy_correlation_depends_on():
    con = _con()
    rows = con.execute(
        """select count(*), count(*) filter (where parent_id is null)
           from entity_baseline where entity_type in ('office', 'vendor')""").fetchone()
    cedar = con.execute(
        """select count(distinct entity_id) from entity_baseline
           where entity_type = 'office' and entity_id like '%Cedar Ridge%'""").fetchone()[0]
    con.close()
    if not rows[0]:
        pytest.skip("no baselines yet - run `make replay`")
    assert rows[1] == 0, "every office and vendor baseline needs a parent_id"
    # Cedar Ridge Office exists under vanta-Aus at 85.1% and orbit-Slc at 71.0%.
    # Merging them invents a site that does not exist.
    assert cedar == 2


@needs_data
def test_july_21_raises_the_site_and_both_of_its_vendors_under_one_parent():
    con = _con()
    rows = con.execute(
        """select entity_type, entity_id, parent_id from signal
           where as_of = date '2026-07-21' and detector = 'punctuality_drop'
             and parent_id = 'vanta-Aus'""").fetchall()
    con.close()
    if not rows:
        pytest.skip("no signals yet - run `make replay`")
    ids = {r[1] for r in rows}
    assert "vanta-Aus / Cedar Ridge Office" in ids
    assert "Sneha Mikhailov Travel" in ids
    assert "Meera Pavlov Travel" in ids
    assert all(r[2] == "vanta-Aus" for r in rows)


# ------------------------------------------------------------------- the router

httpx = pytest.importorskip("httpx", reason="httpx is needed for the endpoint tests")


def _client():
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


@needs_data
def test_the_four_endpoints_return_the_contract_shapes():
    client = _client()

    trust = client.get("/api/sense/field-trust")
    assert trust.status_code == 200
    assert trust.json()[0]["verdict"] == "quarantined"     # worst first
    assert set(trust.json()[0]) >= {"table_name", "column_name", "verdict", "trust",
                                    "test_name", "evidence", "computed_on"}

    signals = client.get("/api/sense/signals", params={"date": "2026-07-21"})
    assert signals.status_code == 200
    for row in signals.json():
        assert isinstance(row["evidence"], list)           # parsed, not a string
        assert set(row) >= {"signal_id", "as_of", "detector", "severity", "entity_type",
                            "entity_id", "parent_id", "metric", "value", "baseline", "z",
                            "n", "direction", "headline", "evidence", "created_at"}

    base = client.get("/api/sense/baselines",
                      params={"entity_type": "office", "metric": "ota15",
                              "entity_id": "vanta-Aus / Cedar Ridge Office",
                              "from": "2026-07-18", "to": "2026-07-22"})
    assert base.status_code == 200
    assert set(base.json()[0]) >= {"as_of", "baseline_mean", "baseline_sd", "z",
                                   "peer_median", "peer_pctile", "slope_28d"}

    grid = client.get("/api/sense/counterfactual",
                      params={"entity_type": "business_unit", "entity_id": "vanta-Aus",
                              "lever": "schedule_pad_min"})
    assert grid.status_code == 200
    assert all(row["assumption"] for row in grid.json())


def test_a_malformed_date_is_a_400_not_a_500():
    assert _client().get("/api/sense/signals", params={"date": "yesterday"}).status_code == 400
