-- MoveOps derived-state schema. FROZEN.
-- Applied to backend/agent.duckdb, which ATTACHes analytics/mis.duckdb read-only as `mis`.
-- Base tables (read-only, owned by nobody): mis.trips, mis.emp_legs, mis.alerts,
-- mis.bills, mis.feedback.
--
-- Ownership. A table is WRITTEN by exactly one service and READ by any.
--   sense  -> field_trust, entity_baseline, signal, counterfactual
--   reason -> incident, suppression, hypothesis
--   memory -> case_file, prediction, playbook, eval_result
--   frozen -> replay_state
--
-- Conventions
--   * All ids are deterministic: sha1 of the natural key, first 16 hex chars.
--     Re-running a day MUST produce identical ids so replays are idempotent.
--   * `json` columns hold a JSON array or object encoded as text.
--   * All dates are the replay's business date, not wall-clock.
--   * Every numeric claim shown to a user must appear in an `evidence` array,
--     because the faithfulness gate checks output numbers against it.

------------------------------------------------------------------ sense

-- What each source field is worth. Written once at startup, re-tested per window.
create table if not exists field_trust (
    table_name   varchar not null,
    column_name  varchar not null,
    verdict      varchar not null,   -- trusted | degraded | quarantined
    trust        double,             -- 0..1
    test_name    varchar not null,   -- e.g. 'correlation_with_reconstruction'
    evidence     varchar not null,   -- one human sentence, with the number in it
    computed_on  date    not null,
    primary key (table_name, column_name)
);

-- Rolling context for every entity and metric, one row per replay day.
-- This is the context cache: trend and peer are a lookup, never a recompute.
create table if not exists entity_baseline (
    as_of         date    not null,
    entity_type   varchar not null,  -- vendor | office | business_unit | contract | shift
    entity_id     varchar not null,
    parent_id     varchar,           -- office/vendor -> business_unit. Correlation needs this.
    metric        varchar not null,  -- ota15 | ack_minutes | noshow_pct | seat_util | cost_per_trip
    value         double,
    n             bigint,
    baseline_mean double,            -- trailing 28d, SAME WEEKDAY ONLY
    baseline_sd   double,
    baseline_n    bigint,            -- observations behind the baseline
    z             double,
    peer_group    varchar,
    peer_median   double,
    peer_pctile   double,
    slope_28d     double,
    primary key (as_of, entity_type, entity_id, metric)
);

-- A raw detection, before any suppression. reason/ decides what survives.
create table if not exists signal (
    signal_id   varchar primary key,
    as_of       date    not null,
    detector    varchar not null,  -- punctuality_drop | metric_integrity | alert_ack_sla
                                   -- | safety_cluster | escort_breach | noshow_spike
                                   -- | billing_anomaly | vendor_chronic
    severity    varchar not null,  -- critical | high | medium | low
    entity_type varchar not null,
    entity_id   varchar not null,
    parent_id   varchar,
    metric      varchar not null,
    value       double,
    baseline    double,
    z           double,
    n           bigint,
    direction   varchar not null,  -- worse | better   ('better' is not automatically good)
    headline    varchar not null,
    evidence    json    not null,  -- [{"claim","value","unit","source"}]
    created_at  timestamp not null
);

-- Precomputed "what if" grid. reason/ reads this; it never recomputes metrics itself.
create table if not exists counterfactual (
    as_of           date    not null,
    entity_type     varchar not null,
    entity_id       varchar not null,
    lever           varchar not null,  -- schedule_pad_min | vendor_substitute | fleet_add
    param           varchar not null,  -- '5' | '10' | 'Priya Mikhailov Travel' | '250'
    metric          varchar not null,
    baseline_value  double,
    projected_value double,
    delta           double,
    n               bigint,
    assumption      varchar not null,  -- the honest caveat, always populated
    confidence      varchar not null,  -- exact | estimated | weak
    primary key (as_of, entity_type, entity_id, lever, param, metric)
);

------------------------------------------------------------------ reason

-- A signal that survived suppression and earned a human's attention.
create table if not exists incident (
    incident_id    varchar primary key,
    opened_on      date    not null,
    status         varchar not null,  -- open | recurring | structural | closed
    severity       varchar not null,
    entity_type    varchar not null,
    entity_id      varchar not null,
    detector       varchar not null,
    headline       varchar not null,
    narrative      varchar not null,  -- the paragraph a human reads
    context        json    not null,  -- {"trend":{},"peer":{},"threshold":{},"impact":{}}
                                      -- ALL FOUR KEYS REQUIRED. The trace gate checks this.
    signal_ids     json    not null,  -- every signal folded in, including suppressed children
    recommendation json,              -- {"action","owner","due","expected_effect",
                                      --  "confidence","assumption"}
    persona        varchar not null,  -- transport_manager | transport_head | line_manager
    created_at     timestamp not null
);

-- Why a signal did NOT become an incident. This table is a headline demo surface,
-- so `explanation` must read as a sentence a transport manager would accept.
create table if not exists suppression (
    suppression_id     varchar primary key,
    as_of              date    not null,
    signal_id          varchar not null,
    reason_code        varchar not null,  -- composition | small_sample | child_of_parent
                                          -- | known_pattern | target_moved
    explanation        varchar not null,  -- must contain the number that justifies it
    evidence           json    not null,
    parent_incident_id varchar,           -- required when reason_code = 'child_of_parent'
    created_at         timestamp not null
);

-- Competing explanations tested against data. At least two per incident,
-- at least one refuted, or the trace gate fails the incident.
create table if not exists hypothesis (
    hypothesis_id varchar primary key,
    incident_id   varchar not null,
    name          varchar not null,
    statement     varchar not null,
    verdict       varchar not null,  -- supported | refuted | inconclusive
    test_sql      varchar not null,  -- the query actually run
    result        json    not null,
    reasoning     varchar not null,  -- one sentence tying result to verdict
    rank          int     not null
);

------------------------------------------------------------------ memory

-- The episodic store. One file per recurring problem, not per occurrence.
-- `signature` is the retrieval key: detector|entity_type|entity_id.
create table if not exists case_file (
    case_id      varchar primary key,
    signature    varchar not null,
    entity_type  varchar not null,
    entity_id    varchar not null,
    opened_on    date    not null,
    last_seen_on date    not null,
    occurrences  int     not null,
    status       varchar not null,  -- open | recurring | structural | resolved
    incident_ids json    not null,
    diagnosis    varchar not null,  -- the current best explanation, updated as evidence lands
    created_at   timestamp not null,
    updated_at   timestamp not null
);

-- How memory is earned. The dataset contains no interventions, so the agent cannot
-- learn from actions taken. It learns from whether its own DIAGNOSES held up.
-- Every open case carries a falsifiable prediction with a date.
create table if not exists prediction (
    prediction_id varchar primary key,
    case_id       varchar not null,
    made_on       date    not null,
    verify_on     date    not null,
    statement     varchar not null,  -- plain English, shown in the UI
    metric        varchar not null,
    entity_type   varchar not null,
    entity_id     varchar not null,
    predicate     varchar not null,  -- lt | gt | between
    threshold     double  not null,
    threshold_hi  double,
    outcome       varchar,           -- null until verify_on: confirmed | refuted | unverifiable
    observed      double,
    verified_on   date
);

-- Procedural memory. An entry is created ONLY by promotion:
-- >= 2 predictions confirmed for the same signature. Never written by assertion.
create table if not exists playbook (
    playbook_id varchar primary key,
    signature   varchar not null,  -- detector|entity_type
    action      varchar not null,
    n_cases     int     not null,
    n_confirmed int     not null,
    confidence  double  not null,  -- n_confirmed / n_cases
    evidence    json    not null,  -- the case_ids and prediction_ids behind it
    promoted_on date    not null,
    updated_on  date    not null
);

-- Evaluation output. Four gates, all deterministic. No LLM anywhere in this table.
create table if not exists eval_result (
    run_id      varchar not null,
    gate        varchar not null,  -- faithfulness | detection | trace_schema | behaviour
    metric      varchar not null,
    value       double,
    passed      boolean,
    detail      json,
    computed_at timestamp not null,
    primary key (run_id, gate, metric)
);

------------------------------------------------------------------ frozen

-- The replay cursor. The UI polls this; the batch replay advances it.
create table if not exists replay_state (
    id             int primary key,
    current_day    date,
    first_day      date,
    last_day       date,
    status         varchar,   -- idle | running | paused | done
    days_done      int,
    trips_seen     bigint,
    signals_raised int,
    suppressed     int,
    incidents_open int,
    updated_at     timestamp
);
