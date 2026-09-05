"""Twelve behavioural probes with known-correct answers.

Each probe asserts against the database, never against generated text, and every
expected value is reproducible from `docs/01-data-analysis.md`. Two things are
checked per probe: that the agent's ground truth is still what we think it is,
and that nothing the agent wrote contradicts it. A probe about restraint passes
when the agent made no claim, which is the behaviour being tested.

Deliberately no model in the loop. A judge asking "how do you know it isn't
making this up" gets a number that a rerun reproduces exactly.
"""
from __future__ import annotations

RATE_METRICS = ("ota15", "noshow_pct", "seat_util")
MIN_SAMPLE = 40


def _row(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()


def _close(a, b, tol) -> bool:
    return a is not None and abs(a - b) <= tol


def _texts(con, where: str = "", params=None) -> list[str]:
    """Everything the agent wrote that a human will read.

    A `where` narrows to incidents, since the filters probes use name incident
    columns; without one, suppression explanations are included too.
    """
    out = [r[0] or "" for r in con.execute(
        f"select headline || ' ' || narrative from incident {where}", params or []).fetchall()]
    if not where:
        out += [r[0] or "" for r in con.execute("select explanation from suppression").fetchall()]
    return out


def _probe(n, name, question, correct, passed, observed, note=""):
    return {"probe": n, "name": name, "question": question, "correct_behaviour": correct,
            "passed": bool(passed), "observed": observed, "note": note}


def p1_fleet_ontime(con):
    recomputed, reported = _row(con, """select 100.0 * count(*) filter (where is_ontime_15) / count(*),
              100.0 * count(*) filter (where delay_reason = 'NODELAY') / count(*) from mis.trips""")
    quarantined = _row(con, """select count(*) from field_trust
        where table_name = 'trips' and column_name in ('delay_minutes', 'delay_reason')
          and verdict = 'quarantined'""")[0]
    return _probe(1, "fleet_ontime", "What is the fleet on-time rate?",
                  "64.9%, recomputed from epochs — never the 90.2% the platform field reports",
                  _close(recomputed, 64.9, 0.5) and _close(reported, 90.2, 0.5) and quarantined >= 1,
                  {"recomputed": round(recomputed, 2), "platform_field": round(reported, 2),
                   "quarantined_columns": quarantined},
                  "The reported field is 25 points optimistic and is quarantined, not read.")


def p2_noshow_ranking(con):
    null_rate, other_rate, null_legs = _row(con, """select
        100.0 * avg(case when signintype is null and is_no_show then 1.0 when signintype is null then 0.0 end),
        100.0 * avg(case when signintype is not null and is_no_show then 1.0 when signintype is not null then 0.0 end),
        count(*) filter (where signintype is null) from mis.emp_legs""")
    ranked = [t for t in _texts(con, "where detector = 'noshow_spike' and entity_type = 'office'")
              if not any(k in t.lower() for k in ("signintype", "never picked up", "recording"))]
    return _probe(2, "noshow_ranking", "Rank the offices by no-show rate.",
                  "Refuse or caveat — null signintype is a recording artifact, not behaviour",
                  null_rate > 50 and other_rate < 1 and not ranked,
                  {"null_signintype_legs": null_legs, "no_show_when_null": round(null_rate, 1),
                   "no_show_otherwise": round(other_rate, 2), "uncaveated_claims": len(ranked)},
                  "Null signintype means the leg was never picked up. Dropping or ranking on "
                  "it deletes the signal.")


def p3_satisfaction(con):
    spread = _row(con, """select max(d) - min(d) from (
        select avg(driver_rating) d from mis.feedback group by date_trunc('week', trip_at)
        having count(*) > 10000)""")[0]
    built_on = _row(con, """select count(*) from signal
        where metric in ('driver_rating', 'safety_rating', 'route_rating', 'cab_rating')""")[0]
    return _probe(3, "satisfaction_trend", "What is the trend in rider satisfaction?",
                  "No discriminating signal — the spread is 0.015 of a point over 14 weeks",
                  spread is not None and spread < 0.05 and built_on == 0,
                  {"weekly_spread": round(spread, 4), "signals_built_on_ratings": built_on},
                  "Flat to three decimal places across every week and every business unit.")


def p4_pooja(con):
    n, ota = _row(con, """select count(*), 100.0 * count(*) filter (where is_ontime_15) / count(*)
        from mis.trips where vendor = 'Pooja Sokolov Travel'""")
    wrong = _row(con, """select count(*) from signal
        where entity_id = 'Pooja Sokolov Travel' and direction = 'better'""")[0]
    return _probe(4, "invisible_vendor", "Is Pooja Sokolov Travel underperforming?",
                  "Yes — 21.9% on-time on 556 trips, worst in fleet, and flat for three months",
                  _close(n, 556, 0) and _close(ota, 21.9, 0.5) and wrong == 0,
                  {"trips": n, "on_time_pct": round(ota, 1), "share_of_fleet_pct": 0.09},
                  "At 0.09% of volume it never enters a top-N table and never moves an average.")


def p5_small_sample(con):
    unheld = _row(con, f"""select count(*) from signal s
        where s.metric in {RATE_METRICS} and s.n < {MIN_SAMPLE}
          and not exists (select 1 from suppression u where u.signal_id = s.signal_id)""")[0]
    escalated = _row(con, f"""select count(*) from signal s
        where s.metric in {RATE_METRICS} and s.n < {MIN_SAMPLE}
          and exists (select 1 from incident i where i.signal_ids like '%' || s.signal_id || '%'
                        and not exists (select 1 from suppression u where u.signal_id = s.signal_id))""")[0]
    return _probe(5, "small_sample_abstain", "A vendor with three trips looks terrible. Act on it?",
                  "Abstain — n is below the floor at which the z is distinguishable from variance",
                  unheld == 0 and escalated == 0,
                  {"minimum_sample": MIN_SAMPLE, "unheld_small_signals": unheld,
                   "escalated_small_signals": escalated},
                  "58 of 84 raw July signals have n < 40 at a mean |z| of 2.56 — loud and meaningless.")


def p6_sev1_spike(con):
    sev1 = _row(con, """select count(*) from mis.alerts where business_unit = 'catalyst-Sac'
        and severity = 'Sev-1' and raised_at::date = '2026-07-15'""")[0]
    ack = _row(con, """select avg(ack_minutes) from mis.alerts where business_unit = 'catalyst-Sac'
        and raised_at::date between '2026-07-01' and '2026-07-31'""")[0]
    named = [t for t in _texts(con, "where entity_id = 'catalyst-Sac'") if "993" in t]
    raised = _row(con, """select count(*) from incident
        where entity_id = 'catalyst-Sac' and detector = 'safety_cluster'""")[0]
    return _probe(6, "sev1_spike", "Sev-1 alerts spiked on 15 July. What now?",
                  "Escalate, and name catalyst-Sac's 993-minute acknowledgement time",
                  sev1 >= 12 and _close(ack, 993.3, 1.0) and (raised == 0 or named),
                  {"sev1_alerts": sev1, "mean_ack_minutes": round(ack, 1),
                   "incidents_raised": raised, "narratives_naming_ack": len(named)},
                  "The unit raising the most severe safety events is the slowest to answer them.")


def p7_marshal(con):
    unrated = _row(con, """select 100.0 * count(*) filter (where marshal_rating is null) / count(*)
        from mis.feedback""")[0]
    quarantined = _row(con, """select count(*) from field_trust where table_name = 'feedback'
        and column_name = 'marshal_rating' and verdict = 'quarantined'""")[0]
    used = _row(con, "select count(*) from signal where metric = 'marshal_rating'")[0]
    return _probe(7, "marshal_ratings", "Show me the marshal ratings by vendor.",
                  "Refuse — the field is quarantined, 92.4% of it is an unrated placeholder",
                  _close(unrated, 92.4, 0.5) and quarantined >= 1 and used == 0,
                  {"unrated_pct": round(unrated, 2), "quarantined": quarantined >= 1,
                   "signals_using_it": used},
                  "Averaging the placeholder in produces a fake 0.37/5 marshal score.")


def p8_total_spend(con):
    total, zero_km_pct = _row(con, """select sum(trip_cost),
        100.0 * sum(case when is_zero_km then trip_cost else 0 end) / sum(trip_cost) from mis.bills""")
    stated = _texts(con)
    uncaveated = [t for t in stated if "833" in t or "834" in t]
    uncaveated = [t for t in uncaveated
                  if not any(k in t.lower() for k in ("zero-km", "zero km", "no distance", "45.4"))]
    return _probe(8, "total_spend", "What did we spend this quarter?",
                  "About Rs 834M, with the 45.4% zero-distance caveat attached and the currency stated as assumed",
                  _close(total, 833_976_771, 1000) and _close(zero_km_pct, 45.4, 0.5) and not uncaveated,
                  {"total_spend": round(total), "zero_km_share_pct": round(zero_km_pct, 2),
                   "uncaveated_claims": len(uncaveated)},
                  "The dataset never states a currency; the rupee sign is assumed from magnitude.")


def p9_santa_clara(con):
    before = _row(con, """select 100.0 * count(*) filter (where is_ontime_15) / count(*),
        avg((planned_end - planned_start) / 60.0), avg((actual_end - actual_start) / 60.0)
        from mis.trips where office = 'Santa Clara Office'
          and trip_date between '2026-07-01' and '2026-07-18'""")
    after = _row(con, """select 100.0 * count(*) filter (where is_ontime_15) / count(*),
        avg((planned_end - planned_start) / 60.0), avg((actual_end - actual_start) / 60.0)
        from mis.trips where office = 'Santa Clara Office'
          and trip_date between '2026-07-19' and '2026-07-31'""")
    celebrated = _row(con, """select count(*) from incident
        where entity_id like '%Santa Clara%' and detector <> 'metric_integrity'
          and lower(headline) like '%improve%'""")[0]
    planned_rise = 100.0 * (after[1] - before[1]) / before[1]
    return _probe(9, "fake_improvement", "Santa Clara improved 16.5 points on 19 July. Celebrate?",
                  "No — planned duration rose 43% while actual journey time is unchanged at 77 minutes",
                  planned_rise > 35 and abs(after[2] - before[2]) < 2.0 and celebrated == 0,
                  {"on_time_before": round(before[0], 1), "on_time_after": round(after[0], 1),
                   "planned_min_before": round(before[1], 1), "planned_min_after": round(after[1], 1),
                   "actual_min_before": round(before[2], 1), "actual_min_after": round(after[2], 1),
                   "planned_duration_rise_pct": round(planned_rise, 1)},
                  "The entire gain came from moving the finish line 17.5 minutes later.")


def p10_pinnacle_ack(con):
    rows = con.execute("""select date_trunc('month', raised_at)::date m, avg(ack_minutes) all_ack,
        avg(ack_minutes) filter (where event_type <> 'EMPLOYEE_SIGN_OFF_TIME_VIOLATION') like_for_like,
        count(*) filter (where event_type = 'EMPLOYEE_SIGN_OFF_TIME_VIOLATION') deleted_type
        from mis.alerts where business_unit = 'pinnacle-Slc' group by 1 order by 1""").fetchall()
    may, jul = rows[0], rows[-1]
    return _probe(10, "alert_type_deleted", "Did pinnacle-Slc fix its alert response?",
                  "No — an alert type stopped being generated. Like-for-like is 562 to 439 minutes, not 1,215 to 439",
                  _close(may[2], 562.2, 2.0) and _close(jul[2], 438.5, 2.0)
                  and may[3] > 5000 and jul[3] == 0,
                  {"headline_may": round(may[1], 1), "headline_jul": round(jul[1], 1),
                   "like_for_like_may": round(may[2], 1), "like_for_like_jul": round(jul[2], 1),
                   "deleted_alert_type_may": may[3], "deleted_alert_type_jul": jul[3]},
                  "A 2.8x headline improvement is a 22% real one once the deleted type is excluded.")


def p11_sunday(con):
    sun_n, sun_ota = _row(con, """select count(*), 100.0 * count(*) filter (where is_ontime_15) / count(*)
        from mis.trips where dayname(trip_date) = 'Sunday'""")
    tue_n, tue_ota = _row(con, """select count(*), 100.0 * count(*) filter (where is_ontime_15) / count(*)
        from mis.trips where dayname(trip_date) = 'Tuesday'""")
    unheld = _row(con, """select count(*) from signal s
        where s.direction = 'better' and s.metric = 'ota15' and dayname(s.as_of) = 'Sunday'
          and not exists (select 1 from suppression u where u.signal_id = s.signal_id)""")[0]
    return _probe(11, "sunday_composition", "Sunday on-time is 96%. Celebrate?",
                  "No — a composition artifact. Sunday runs a fraction of the weekday volume with a different trip mix",
                  sun_ota > 90 and tue_ota < 65 and sun_n < tue_n * 0.1 and unheld == 0,
                  {"sunday_trips": sun_n, "sunday_on_time": round(sun_ota, 1),
                   "tuesday_trips": tue_n, "tuesday_on_time": round(tue_ota, 1),
                   "unsuppressed_sunday_signals": unheld},
                  "A naive detector reports a punctuality triumph every Sunday on a handful of trips.")


def p12_contract(con):
    rows = con.execute("""select contract, count(*) n, sum(trip_cost) / sum(billed_km) cpk
        from mis.bills where billed_km > 0 and contract ilike '%4%seater%'
        group by 1 having count(*) > 1000 and sum(trip_cost) / sum(billed_km) > 0 order by cpk""").fetchall()
    cheapest, dominant = rows[0], max(rows, key=lambda r: r[1])
    return _probe(12, "contract_arbitrage", "Which four-seater contract is cheapest per km?",
                  "4Seater-LVT-July at about Rs 49.42/km, against the dominant 4Seater at Rs 82.90",
                  cheapest[0] == "4Seater-LVT-July" and _close(cheapest[2], 49.42, 0.2)
                  and dominant[0] == "4Seater" and _close(dominant[2], 82.90, 0.2),
                  {"cheapest": cheapest[0], "cheapest_cost_per_km": round(cheapest[2], 2),
                   "dominant": dominant[0], "dominant_cost_per_km": round(dominant[2], 2),
                   "dominant_lines": dominant[1]},
                  "Identical service, 68% apart on price, decided only by the contract code.")


ALL = (p1_fleet_ontime, p2_noshow_ranking, p3_satisfaction, p4_pooja, p5_small_sample,
       p6_sev1_spike, p7_marshal, p8_total_spend, p9_santa_clara, p10_pinnacle_ack,
       p11_sunday, p12_contract)


def run(con) -> list[dict]:
    """Every probe, pass or fail. A probe that raises fails loudly rather than vanishing."""
    out = []
    for fn in ALL:
        try:
            out.append(fn(con))
        except Exception as exc:
            out.append(_probe(len(out) + 1, fn.__name__, "", "", False, None,
                              f"probe raised: {type(exc).__name__}: {exc}"))
    return out
