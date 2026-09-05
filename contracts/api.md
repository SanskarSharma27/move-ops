# API contract — FROZEN

Base URL `http://localhost:8000`. Every path is mounted automatically from the
service folder name. CORS is open to `http://localhost:4200`.

**For the frontend:** every response shape below is authoritative. Build against
these, and against `contracts/fixtures/*.json` which contain exactly these shapes with
real values. Do not wait for a backend to exist.

**For backend owners:** you may add endpoints under your own prefix. You may not
change a shape defined here, or add an endpoint under someone else's prefix.

Conventions — dates are `YYYY-MM-DD`; `json` columns arrive as parsed objects, not
strings; list endpoints return a bare JSON array; unknown ids return `404` with
`{"detail": "..."}`.

---

## Replay — owned by `backend/main.py` (frozen)

```
GET  /api/health                      -> {"status":"ok","services":["sense","reason"]}
GET  /api/replay/state
POST /api/replay/play
POST /api/replay/pause
POST /api/replay/reset
POST /api/replay/seek?day=2026-07-21
```

`state` returns:

```json
{"id":1,"current_day":"2026-07-21","first_day":"2026-07-01","last_day":"2026-07-31",
 "status":"paused","days_done":0,"trips_seen":0,"signals_raised":8,
 "suppressed":5,"incidents_open":3,"updated_at":"2026-09-05T11:57:03"}
```

The replay is **precomputed** by `make replay`. These endpoints move a cursor over the
result, so the demo never waits on computation and cannot fail live. The UI drives the
clock: poll `state`, or advance locally and call `seek`.

---

## `/api/sense` — owner A

```
GET /api/sense/field-trust
GET /api/sense/signals?date=&entity_type=&detector=&limit=100
GET /api/sense/baselines?entity_type=&entity_id=&metric=&from=&to=
GET /api/sense/counterfactual?entity_type=&entity_id=&lever=
```

`field-trust` — every column the agent audited, worst first:

```json
[{"table_name":"trips","column_name":"delay_minutes","verdict":"quarantined",
  "trust":0.04,"test_name":"correlation_with_reconstruction",
  "evidence":"Zero on 90.2% of trips and correlates 0.04 with departure slip recomputed from epochs. Cannot be used for punctuality.",
  "computed_on":"2026-07-01"}]
```

`signals` — raw detections for a date, before suppression:

```json
[{"signal_id":"a1b2c3d4e5f60001","as_of":"2026-07-21","detector":"punctuality_drop",
  "severity":"critical","entity_type":"office",
  "entity_id":"vanta-Aus / Cedar Ridge Office","parent_id":"vanta-Aus",
  "metric":"ota15","value":59.3,"baseline":88.9,"z":-4.18,"n":978,"direction":"worse",
  "headline":"Cedar Ridge on-time arrival fell to 59.3% against an 88.9% baseline",
  "evidence":[{"claim":"on-time arrival","value":59.3,"unit":"%","source":"mis.trips"}],
  "created_at":"2026-07-21T23:59:00"}]
```

`baselines` — the sparkline source. One row per day per entity+metric, with
`baseline_mean`, `baseline_sd`, `z`, `peer_median`, `peer_pctile`, `slope_28d`.

`counterfactual` — precomputed projections:

```json
[{"as_of":"2026-07-31","entity_type":"business_unit","entity_id":"vanta-Aus",
  "lever":"schedule_pad_min","param":"10","metric":"ota15",
  "baseline_value":81.5,"projected_value":90.4,"delta":8.9,"n":23584,
  "assumption":"Recomputed against the same trips with planned_end shifted 10 minutes later. This is the same schedule padding Santa Clara used on 19 July; recommend only alongside a real journey-time fix.",
  "confidence":"exact"}]
```

---

## `/api/reason` — owner B

```
GET  /api/reason/incidents?date=&status=&persona=&limit=50
GET  /api/reason/incidents/{incident_id}
GET  /api/reason/suppressions?date=&reason_code=&limit=100
GET  /api/reason/summary?date=
POST /api/reason/whatif
```

`incidents/{id}` returns the incident plus its hypotheses and the signals it absorbed:

```json
{"incident_id":"inc0000000000001","opened_on":"2026-07-21","status":"recurring",
 "severity":"critical","entity_type":"office",
 "entity_id":"vanta-Aus / Cedar Ridge Office","detector":"punctuality_drop",
 "headline":"Cedar Ridge site-wide punctuality collapse — third occurrence this month",
 "narrative":"On-time arrival at Cedar Ridge fell to 59.3% ...",
 "context":{
   "trend":{"statement":"Third occurrence in 14 days, deepening each time",
            "values":{"jul_8":72.9,"jul_15":77.8,"jul_21":59.3},"unit":"%"},
   "peer":{"statement":"Worst site-day in the business unit this month",
           "peer_group":"vanta-Aus offices","peer_median":85.1,"pctile":2,"unit":"%"},
   "threshold":{"statement":"Below the 80% on-time target for the fourth time this month",
                "target":80.0,"actual":59.3,"unit":"%"},
   "impact":{"statement":"398 employees arrived more than 15 minutes late",
             "value":398,"unit":"employees"}},
 "signal_ids":["a1b2c3d4e5f60001","a1b2c3d4e5f60002","a1b2c3d4e5f60003"],
 "recommendation":{"action":"Add 10 minutes to planned duration for Tuesday and Wednesday Cedar Ridge routes...",
   "owner":"Transport Manager, vanta-Aus","due":"2026-07-28",
   "expected_effect":"On-time arrival rises from 81.5% to 90.4% across the business unit",
   "confidence":"exact","assumption":"Recomputed against the same trip distribution. This moves the metric, not the commute..."},
 "persona":"transport_manager","created_at":"2026-07-21T23:59:30",
 "hypotheses":[
   {"hypothesis_id":"hyp0000000000001","name":"vendor_failure",
    "statement":"One or two vendors degraded and dragged the site average down.",
    "verdict":"refuted","test_sql":"select vendor, ...",
    "result":{"Sneha Mikhailov Travel":63.0,"Priya Mikhailov Travel":56.0},
    "reasoning":"All five vendors fell into a 7-point band...","rank":1}],
 "signals":[ ... full signal objects ... ]}
```

`suppressions` — the demo centrepiece. Every discarded signal with its reason:

```json
[{"suppression_id":"sup0000000000001","as_of":"2026-07-21",
  "signal_id":"a1b2c3d4e5f60002","reason_code":"child_of_parent",
  "explanation":"Folded into the Cedar Ridge site incident. All five vendors at the site fell into a 7-point band that day (56.0% to 63.0%), so this is not a Sneha Mikhailov Travel failure and no vendor penalty is warranted.",
  "evidence":[{"claim":"vendors affected at the site","value":5,"unit":"of 5","source":"mis.trips"}],
  "parent_incident_id":"inc0000000000001","created_at":"2026-07-21T23:59:20",
  "signal":{ ... the suppressed signal ... }}]
```

`summary` — the funnel headline, for the top strip:

```json
{"date":"2026-07-21","raw_signals":11,"suppressed":8,"incidents":3,
 "by_reason":{"composition":4,"small_sample":2,"child_of_parent":2},
 "window":{"raw_signals":84,"suppressed":74,"incidents":10}}
```

`whatif` — request `{"incident_id":"inc0000000000001","lever":"vendor_substitute",
"param":"Rohan Mikhailov Travel"}`, response is one `counterfactual` object plus a
`narrative` string. **Must return a considered answer when the lever is a bad idea**,
not an error:

```json
{"lever":"vendor_substitute","param":"Rohan Mikhailov Travel","metric":"ota15",
 "baseline_value":47.8,"projected_value":47.2,"delta":-0.6,"n":13091,
 "confidence":"estimated",
 "assumption":"Applies Rohan's observed 41.3% site on-time to Vikram's trips.",
 "narrative":"Rohan Mikhailov Travel runs 41.3% on-time at this site against Vikram's 43.2%. Substituting would move site on-time from 47.8% to 47.2% — slightly worse. Not recommended."}
```

---

## `/api/memory` — owner C

```
GET /api/memory/cases?entity_id=&status=&limit=50
GET /api/memory/cases/{case_id}
GET /api/memory/predictions?status=&case_id=
GET /api/memory/playbook
GET /api/memory/report-card
GET /api/memory/eval?gate=
```

`cases/{id}` returns the case with its predictions and linked incidents:

```json
{"case_id":"CASE-0001","signature":"punctuality_drop|office|vanta-Aus / Cedar Ridge Office",
 "entity_type":"office","entity_id":"vanta-Aus / Cedar Ridge Office",
 "opened_on":"2026-07-08","last_seen_on":"2026-07-29","occurrences":4,
 "status":"structural","incident_ids":["inc0000000000001"],
 "diagnosis":"Recurring Tuesday and Wednesday capacity shortfall at Cedar Ridge...",
 "predictions":[
   {"prediction_id":"prd0000000000001","made_on":"2026-07-08","verify_on":"2026-07-15",
    "statement":"Cedar Ridge will fall below 80% on-time again within seven days, on a Tuesday or Wednesday.",
    "metric":"ota15","predicate":"lt","threshold":80.0,
    "outcome":"confirmed","observed":77.8,"verified_on":"2026-07-15"}],
 "prediction_record":{"confirmed":3,"refuted":0,"pending":1},
 "created_at":"2026-07-08T23:59:40","updated_at":"2026-07-29T23:59:40"}
```

`report-card` — four numbers, always on screen:

```json
{"run_id":"seed","generated_at":"2026-07-31T23:59:59",
 "faithfulness":{"unsourced_number_rate":0.0,"statements_checked":1247,
                 "numbers_extracted":4183,"passed":true},
 "detection":{"precision":0.86,"recall":0.75,"median_lead_days":19.0,"passed":true},
 "trace_schema":{"complete_traces":1.0,"incidents":3,"complete":3,"passed":true},
 "behaviour":{"probes_passed":12,"probes_total":12,"failed":[],"passed":true}}
```

---

## Mock mode

`contracts/fixtures/*.json` is one file per table, each an array of rows in exactly the
shape above. The frontend reads them directly when `environment.useMock` is true, so
the UI is fully functional before any backend exists. Two differences to handle:

- Fixture `json` columns are already objects. So are API responses. Same shape.
- Fixtures are a flat table dump; composite endpoints (`incidents/{id}`,
  `cases/{id}`, `report-card`, `summary`) must be assembled client-side in mock mode
  by joining on the id fields. Keep that join in the mock service only.
