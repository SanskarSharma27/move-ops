"""Memory tests. Every one runs against a throwaway database with the real raw
data attached, so the assertions are about behaviour, not about fixtures.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pytest
from common import MIS_DB, SCHEMA, as_json, make_id, upsert

from memory import cases, evaluate, pipeline, playbook, probes, verify

CEDAR = "vanta-Aus / Cedar Ridge Office"
JUL = dt.date(2026, 7, 21)


@pytest.fixture
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "agent.duckdb"))
    c.execute(f"attach '{MIS_DB}' as mis (read_only)")
    c.execute(SCHEMA.read_text())
    # The columns the agent must have audited before it is allowed to answer.
    upsert(c, "field_trust", [
        dict(table_name=t, column_name=col, verdict="quarantined", trust=0.04,
             test_name="correlation_with_reconstruction", evidence="quarantined for the test",
             computed_on=dt.date(2026, 7, 1))
        for t, col in (("trips", "delay_minutes"), ("trips", "delay_reason"),
                       ("feedback", "marshal_rating"))],
        key=("table_name", "column_name"))
    yield c
    c.close()


def add_incident(con, incident_id, opened_on, *, detector="punctuality_drop",
                 entity_type="office", entity_id=CEDAR, narrative="Nothing numeric here.",
                 context=None, evidence=None):
    context = context if context is not None else {
        "trend": {"statement": "third occurrence"}, "peer": {"statement": "worst site"},
        "threshold": {"statement": "below target", "target": 80.0},
        "impact": {"statement": "riders late", "value": 398, "unit": "employees"}}
    upsert(con, "signal", [dict(
        signal_id=incident_id + "-s", as_of=opened_on, detector=detector, severity="critical",
        entity_type=entity_type, entity_id=entity_id, parent_id=None, metric="ota15",
        value=59.3, baseline=88.9, z=-4.18, n=978, direction="worse",
        headline="signal", evidence=as_json(evidence or []),
        created_at=dt.datetime(2026, 7, 1))], key="signal_id")
    upsert(con, "incident", [dict(
        incident_id=incident_id, opened_on=opened_on, status="open", severity="critical",
        entity_type=entity_type, entity_id=entity_id, detector=detector, headline="headline",
        narrative=narrative, context=as_json(context),
        signal_ids=as_json([incident_id + "-s"]),
        recommendation=as_json({"action": "a", "owner": "o", "due": "2026-07-28"}),
        persona="transport_manager", created_at=dt.datetime(2026, 7, 1))], key="incident_id")


def add_hypotheses(con, incident_id, n=2, refuted=True):
    upsert(con, "hypothesis", [dict(
        hypothesis_id=f"{incident_id}-h{i}", incident_id=incident_id, name=f"h{i}",
        statement="s", verdict="refuted" if (refuted and i == 0) else "supported",
        test_sql="select 1", result=as_json({"value": 1}), reasoning="r", rank=i)
        for i in range(n)], key="hypothesis_id")


# 1 --------------------------------------------------------------------------
def test_same_signature_folds_into_one_case(con):
    add_incident(con, "i1", dt.date(2026, 7, 8))
    add_incident(con, "i2", JUL)
    found = cases.collect(con, JUL)
    assert len(found) == 1
    assert found[0]["occurrences"] == 2
    assert found[0]["status"] == "recurring"
    assert found[0]["signature"] == f"punctuality_drop|office|{CEDAR}"


def test_four_occurrences_are_structural(con):
    for i, day in enumerate([dt.date(2026, 7, d) for d in (8, 15, 21, 28)]):
        add_incident(con, f"i{i}", day)
    assert cases.collect(con, dt.date(2026, 7, 28))[0]["status"] == "structural"


# 2 --------------------------------------------------------------------------
def _predict(con, threshold, predicate="lt", made_on=JUL, metric="ota15",
             entity_type="office", entity_id=CEDAR):
    upsert(con, "prediction", [dict(
        prediction_id=make_id("t", threshold, predicate), case_id="c1", made_on=made_on,
        verify_on=made_on + dt.timedelta(days=7), statement="s", metric=metric,
        entity_type=entity_type, entity_id=entity_id, predicate=predicate,
        threshold=threshold, threshold_hi=None, outcome=None, observed=None,
        verified_on=None)], key="prediction_id")


def test_breached_threshold_confirms_and_unbreached_refutes(con):
    _predict(con, 80.0)          # Cedar Ridge runs well under 80 most weekdays
    _predict(con, 10.0)          # it never gets anywhere near 10
    verify.verify_due(con, JUL + dt.timedelta(days=7))
    outcomes = dict(con.execute("select threshold, outcome from prediction").fetchall())
    assert outcomes[80.0] == "confirmed"
    assert outcomes[10.0] == "refuted"


def test_no_qualifying_data_is_unverifiable_not_a_guess(con):
    _predict(con, 80.0, entity_id="vanta-Aus / Nowhere Office")
    verify.verify_due(con, JUL + dt.timedelta(days=7))
    assert con.execute("select outcome from prediction").fetchone()[0] == "unverifiable"


# 3 --------------------------------------------------------------------------
def test_playbook_needs_two_confirmations(con):
    add_incident(con, "i1", JUL)
    case = cases.collect(con, JUL)[0]
    cases.write(con, JUL, [case])
    _predict(con, 80.0)
    con.execute("update prediction set case_id = ?, outcome = 'confirmed', verified_on = ?",
                [case["case_id"], JUL])
    assert playbook.promote(con, JUL) == 0
    assert con.execute("select count(*) from playbook").fetchone()[0] == 0

    _predict(con, 79.0)
    con.execute("update prediction set case_id = ?, outcome = 'confirmed', verified_on = ?",
                [case["case_id"], JUL])
    playbook.promote(con, JUL)
    row = con.execute("select signature, n_confirmed, confidence from playbook").fetchone()
    assert row == ("punctuality_drop|office", 2, 1.0)


# 4 and 5 --------------------------------------------------------------------
def test_faithfulness_flags_a_number_with_no_evidence(con):
    add_incident(con, "i1", JUL, narrative="On-time fell to 59.3% against 88.9%.",
                 evidence=[{"claim": "on-time", "value": 59.3, "unit": "%", "source": "mis.trips"}])
    result = evaluate.faithfulness(con)
    assert result["value"] > 0
    assert not result["passed"]
    assert [e["number"] for e in result["detail"]["examples"]] == ["88.9"]


def test_faithfulness_ignores_years_dates_and_ordinals(con):
    add_incident(con, "i1", JUL,
                 narrative="On 2026-07-21 the third breach since 8 July 2026 held at 59.3%.",
                 evidence=[{"claim": "on-time", "value": 59.3, "unit": "%", "source": "mis.trips"}])
    result = evaluate.faithfulness(con)
    assert result["detail"]["unsourced"] == 0
    assert result["passed"]


def test_faithfulness_covers_memorys_own_prose(con):
    add_incident(con, "i1", JUL, evidence=[])
    add_hypotheses(con, "i1")
    pipeline.run_day(con, JUL)
    result = evaluate.faithfulness(con)
    mine = {r[0] for r in con.execute("select case_id from case_file").fetchall()}
    mine |= {r[0] for r in con.execute("select prediction_id from prediction").fetchall()}
    assert [e for e in result["detail"]["examples"] if e["row"] in mine] == []


# 6 --------------------------------------------------------------------------
def test_trace_schema_fails_an_incident_missing_peer_context(con):
    add_incident(con, "i1", JUL)
    add_hypotheses(con, "i1")
    assert evaluate.trace_schema(con, JUL)["value"] == 1.0

    add_incident(con, "i2", JUL, entity_id="vanta-Aus / Other Office", context={
        "trend": {"statement": "t"}, "threshold": {"statement": "x", "target": 80.0},
        "impact": {"statement": "i", "value": 398, "unit": "employees"}})
    add_hypotheses(con, "i2")
    result = evaluate.trace_schema(con, JUL)
    assert result["value"] == 0.5
    assert "context.peer missing" in result["detail"]["failures"]["i2"]


def test_trace_schema_needs_a_refuted_hypothesis(con):
    add_incident(con, "i1", JUL)
    add_hypotheses(con, "i1", refuted=False)
    result = evaluate.trace_schema(con, JUL)
    assert result["value"] == 0.0
    assert "no refuted hypothesis with a result" in result["detail"]["failures"]["i1"]


# 7 --------------------------------------------------------------------------
def test_every_probe_runs_and_reports(con):
    results = probes.run(con)
    assert len(results) == 12
    assert [r["probe"] for r in results] == list(range(1, 13))
    assert all(isinstance(r["passed"], bool) for r in results)
    failed = [r["name"] for r in results if not r["passed"]]
    assert not failed, f"probes failed: {failed}"


# 8 --------------------------------------------------------------------------
def test_run_day_twice_changes_nothing(con):
    add_incident(con, "i1", dt.date(2026, 7, 8))
    add_incident(con, "i2", JUL)
    add_hypotheses(con, "i1")
    days = (dt.date(2026, 7, 8), JUL, dt.date(2026, 7, 28))

    def snapshot():
        return {t: con.execute(f"select * from {t} order by 1").fetchall()
                for t in ("case_file", "prediction", "playbook", "eval_result")}

    for day in days:
        pipeline.run_day(con, day)
    once = snapshot()
    assert once["case_file"] and once["prediction"]
    for day in days:
        pipeline.run_day(con, day)
    twice = snapshot()
    for table in once:
        assert len(once[table]) == len(twice[table]), table
    # eval_result carries a wall-clock stamp; everything else must be byte-identical.
    for table in ("case_file", "prediction", "playbook"):
        assert once[table] == twice[table], table
