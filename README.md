# MoveOps

An agentic intelligence layer over enterprise commute operations. It **senses** what is
happening in the data, **reasons** about what it means, and **acts** — for the transport
manager, the transport & facilities head, and the line manager.

Built on a 92-day slice of real MoveInSync operations data: 615,546 trips, 1,637,906
rider legs, 620,942 billing lines, 51,699 safety alerts, May–July 2026.

---

## What makes it different

Most tools report the fields they are handed. This one **audits whether the numbers are
telling the truth**, and this dataset gave it three cases to prove it on.

**1. The platform's own punctuality field is wrong by 25 points.**
`delay_reason` reports 90.2% of trips on time. Recomputed from the timestamps, it is
**64.9%**. `delay_minutes` is zero on 90.2% of rows and correlates 0.04 with actual
departure slip. The agent quarantines both fields and refuses to build a metric on them.

**2. A 16.5-point improvement that never happened.**
Santa Clara's on-time arrival jumped from 35.8% to 52.3% on 19 July. Planned trip
duration rose from 40.6 to 58.1 minutes — 43% — while **actual journey time went from
76.4 to 77.0 minutes**, on 14 fewer cabs. The agent rejects the improvement.

**3. An alert-response "fix" that was a deletion.**
pinnacle-Slc's mean acknowledgement time fell from 1,215 to 439 minutes. Not because
anyone got faster: `EMPLOYEE_SIGN_OFF_TIME_VIOLATION` — 7,664 alerts at 1,444 minutes —
simply stopped being generated in June. Like-for-like the gain is 562 → 439. The proof
nothing changed: `DEVICE_NOT_REACHABLE` runs **1,444.4 → 1,444.8 → 1,444.9 minutes**
across the three months.

And it explains what it **ignores**. In July, 144 raw signals become 37 incidents; every
one of the 109 suppressions carries a stated reason a transport manager would accept —
weekend composition, sample too small, a child folded into a site-wide event, a known
pattern reclassified rather than dismissed, or an improvement rejected outright.

---

## Quick start

```bash
make install     # backend deps into .venv (uv)
make build       # analytics/mis.duckdb from the raw CSVs — once, ~15s
make seed        # fixtures into every table, so nothing is empty
make replay      # the 92-day replay through all three services — ~4m20s
make api         # http://localhost:8000/docs
make ui          # http://localhost:4200
make test        # every service's test suite
```

The raw dataset is **not** in this repo. Put the `MoveInSync - Anonymised Trip-Log
Dataset` folder anywhere at or above the repo root and `make build` finds it, or set
`MIS_DATA=/path/to/it`.

---

## How it works

```
raw CSVs ──► analytics/mis.duckdb ──► [replay clock: 1 May … 31 July]
                                            │
                                   sense ──►│ field_trust · entity_baseline
                                            │ signal · counterfactual
                                            ▼
                                  reason ──►│ incident · suppression · hypothesis
                                            ▼
                                  memory ──►│ case_file · prediction · playbook
                                            │ eval_result
                                            ▼
                                       FastAPI ──► Angular
```

The replay is **precomputed**. `make replay` steps through 92 business dates and writes
every detection, decision and verification to `backend/agent.duckdb`. The API then
serves the result and the UI moves a cursor over it, so the demo never waits on
computation and cannot fail live.

**Sensing** builds trailing-28-day, **same-weekday** baselines for every vendor, office
and business unit — necessary because fleet on-time is 96% on Sundays and 60% on
Tuesdays, so a naive rolling mean fires a false positive every week. Seven detectors run
against those baselines, including `metric_integrity`, which fires when a metric moves
in the *good* direction for a bad reason.

**Reasoning** decides what survives. Five suppression rules run in order — `composition`,
`small_sample`, `child_of_parent`, `known_pattern`, `target_moved` — and each surviving
incident gets competing hypotheses tested against SQL, with at least one refuted, plus
four kinds of context (trend, peer, threshold, impact) and a recommendation carrying its
own honest assumption.

**Memory** is earned. This dataset contains **no interventions** — vendor mix is frozen
across all three months, no vendor–office pair starts or stops — so the agent cannot
learn "action X worked". Instead every case carries a falsifiable **prediction** with a
date, later replay days check it, and an action reaches the playbook only after **two or
more confirmed predictions** for the same signature. Nothing is written by assertion.

**Evaluation is fully deterministic. There is no LLM anywhere in this repo.** Four gates:
mechanical numeric faithfulness, held-out detection scoring with lead time, structural
trace assertions, and twelve fixed behavioural probes with known-correct answers.

---

## Layout

| Path | Owner | What |
|---|---|---|
| `contracts/` | frozen | `schema.sql`, `api.md`, `fixtures/` — every cross-boundary interface |
| `backend/main.py` `replay.py` `common.py` `seed.py` | frozen | infrastructure; routers and replay hooks are auto-discovered |
| `backend/services/sense/` | A | field trust, baselines, seven detectors, counterfactual grid |
| `backend/services/reason/` | B | correlation, suppression, hypotheses, narrative |
| `backend/services/memory/` | C | cases, predictions, playbooks, the four eval gates |
| `frontend/` | D | Angular demo surface |
| `analytics/` | shared, read-only | DuckDB workbench, 28 named findings queries |
| `docs/` | shared, read-only | data analysis and demo-case research |
| `prds/` | shared, read-only | one build brief per workstream |

`CONTRACT.md` is the single source of truth for anything crossing a folder boundary.

### Why there are no merge conflicts

Every file has exactly one owner. The two files that normally collide — the router
registry and the pipeline — use **auto-discovery**: drop a `router.py` into
`backend/services/<name>/` and it mounts at `/api/<name>/`; export `run_day` from the
package and the replay calls it. A service that does not exist yet is skipped, so nobody
is ever blocked and nobody edits a shared file to register anything.

---

## Analytics workbench

Every claim in `docs/` is a runnable query.

```bash
uv run analytics/mis.py findings          # all 28
uv run analytics/mis.py findings F-09     # the invisible vendor
uv run analytics/mis.py shell             # interactive
uv run analytics/mis.py "select ..."      # ad-hoc
```

See [`analytics/README.md`](analytics/README.md) for the table reference and every
cleaning decision — each one arguable, each one visible.

---

## Current status

Last verified against a full `make replay` over all 92 days.

| | |
|---|---|
| Replay | 92 days, 615,546 trips, ~4m20s, exit 0 |
| Output | 479 signals · 252 suppressions · 128 incidents · 66 cases · 594 predictions |
| July window | 144 signals → 109 suppressions → 37 incidents |
| API | all 10 endpoints return 200 |
| Tests | 42 passing, 2 failing |
| Trace completeness | 128/128 incidents carry all four context blocks |
| Behavioural probes | 9/12 |
| Faithfulness | 24 unsourced numbers in 1,688 (target: 0) |
| Detection gate | precision 0.036, recall 0.111 — under investigation |

Known issues are tracked in [`docs/03-status.md`](docs/03-status.md).

---

## Documentation

| Doc | What it covers |
|---|---|
| [`CONTRACT.md`](CONTRACT.md) | ownership, vocabulary, helpers, commit discipline, data traps |
| [`docs/00-strategy-brief.md`](docs/00-strategy-brief.md) | the argument, without the numbers |
| [`docs/01-data-analysis.md`](docs/01-data-analysis.md) | every finding, with the query that produces it |
| [`docs/02-demo-cases.md`](docs/02-demo-cases.md) | what fires during the July replay, and why |
| [`docs/03-status.md`](docs/03-status.md) | what works, what does not, what is next |
| [`contracts/api.md`](contracts/api.md) | every endpoint and response shape |
| [`prds/`](prds/) | the four build briefs |
