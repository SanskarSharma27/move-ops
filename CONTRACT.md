# CONTRACT — the single source of truth

Anything that crosses a folder boundary is defined here. **This file is frozen.**
If something in it is wrong, say so in the group chat and the repo owner changes it
once, for everyone. Do not edit it yourself, and do not work around it.

---

## 1. Ownership

| Path | Owner | Rule |
|---|---|---|
| `backend/services/sense/**` | **A** | A's, entirely |
| `backend/services/reason/**` | **B** | B's, entirely |
| `backend/services/memory/**` | **C** | C's, entirely |
| `frontend/**` | **D** | D's, entirely |
| `CONTRACT.md`, `Makefile`, `README.md`, `.gitignore` | frozen | nobody edits |
| `contracts/**` | frozen | nobody edits |
| `backend/main.py`, `replay.py`, `common.py`, `seed.py`, `requirements.txt` | frozen | nobody edits |
| `analytics/**`, `docs/**` | shared | read-only |

You never edit a file outside your own folder. That is what makes four people able to
push to `main` without a single merge conflict.

## 2. Commit discipline — mandatory

**Commit every 30–45 minutes, and always before starting a new file.** Long-lived
uncommitted work is how a hackathon loses four hours.

```bash
git add backend/services/sense          # your folder only
git commit -m "sense: weekday-aware baselines for vendor and office"
git pull --rebase origin main
git push origin main
```

Rules:

- **Never `git add .` or `git add -A`.** Stage your folder by name, every time.
- Message prefix is your service name: `sense:`, `reason:`, `memory:`, `ui:`.
- `git pull --rebase` before every push. Because ownership is disjoint, this will
  never conflict — if it does, you touched someone else's file. Undo that.
- Push working code. A half-finished function that imports cleanly is fine; a file
  with a syntax error breaks the router auto-discovery for everyone.
- Commit your fixtures and tests along with the code they cover.

## 3. How your service plugs in

Two integration points, both discovered automatically. **Nobody registers anything.**

**A. The replay hook.** Export `run_day` from your package:

```python
# backend/services/sense/__init__.py
from .pipeline import run_day          # noqa: F401
```

```python
def run_day(con, day: datetime.date) -> None:
    """Process one business date. Must be idempotent: running twice for the same
    day leaves the database in the same state. Use common.make_id and common.upsert."""
```

`backend/replay.py` calls `sense.run_day` → `reason.run_day` → `memory.run_day` for
each date, and **skips any service that does not exist yet**. You are never blocked
on someone else finishing.

**B. The HTTP hook.** Define a module-level `router` in `router.py`:

```python
# backend/services/sense/router.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/field-trust")
def field_trust(): ...
```

It is mounted at `/api/sense/field-trust` automatically. Path prefixes come from your
folder name — do not set them yourself.

## 4. Helpers you must use

From `backend/common.py`. Import them; do not reimplement them.

```python
from common import db, make_id, as_json, upsert, now, replay_days
from common import FIRST_DAY, LAST_DAY, REPLAY_START, FIXTURES

con = db()                       # agent.duckdb, with raw data attached as `mis`
con = db(read_only=True)         # for routers

make_id("2026-07-21", "punctuality_drop", "office", "Cedar Ridge")
                                 # deterministic 16-hex id — same inputs, same id
upsert(con, "signal", rows, key="signal_id")     # delete-then-insert, re-runnable
as_json({"trend": ...})          # for any `json` column
```

**Idempotency is not optional.** Ids must be built from business values (date, entity,
metric), never from `uuid4()` or a timestamp, or a re-run duplicates every row and the
demo breaks.

One process cannot hold a read-only and a read-write connection to the same DuckDB
file at once. Routers use `db(read_only=True)`; `run_day` gets the shared writable
connection passed in and must not open its own.

## 5. Data model

Full DDL: [`contracts/schema.sql`](contracts/schema.sql). Full API: [`contracts/api.md`](contracts/api.md).

**Raw tables** (read-only, attached as `mis`): `mis.trips` (615,546), `mis.emp_legs`
(1,637,906), `mis.alerts` (51,699), `mis.bills` (620,942), `mis.feedback` (512,873).
Already cleaned and typed — see `analytics/README.md` for every cleaning decision.

**Derived tables** — one writer each:

| Table | Written by | Read by |
|---|---|---|
| `field_trust`, `entity_baseline`, `signal`, `counterfactual` | sense | reason, memory, ui |
| `incident`, `suppression`, `hypothesis` | reason | memory, ui |
| `case_file`, `prediction`, `playbook`, `eval_result` | memory | ui |
| `replay_state` | frozen | ui |

**Write only your own tables.** Read anyone's.

## 6. Vocabulary — use these exact strings

```
entity_type   vendor | office | business_unit | contract | shift
              office entity_id is ALWAYS "business_unit / office" — Cedar Ridge Office
              exists under two business units with a 14-point punctuality gap.

metric        ota15 | ack_minutes | noshow_pct | seat_util | cost_per_trip | sev1_count
detector      punctuality_drop | metric_integrity | alert_ack_sla | safety_cluster
              | escort_breach | noshow_spike | billing_anomaly | vendor_chronic
severity      critical | high | medium | low
direction     worse | better        ('better' is not automatically good — see §7)
reason_code   composition | small_sample | child_of_parent | known_pattern | target_moved
verdict       supported | refuted | inconclusive          (hypothesis)
              trusted | degraded | quarantined            (field_trust)
status        open | recurring | structural | closed       (incident)
              open | recurring | structural | resolved     (case_file)
outcome       confirmed | refuted | unverifiable           (prediction)
confidence    exact | estimated | weak                     (counterfactual, recommendation)
persona       transport_manager | transport_head | line_manager
gate          faithfulness | detection | trace_schema | behaviour
```

`parent_id` carries the hierarchy that correlation depends on: **vendor → business_unit**,
**office → business_unit**, business_unit → null. Populate it on every row.

## 7. Facts about this data that will bite you

Read [`docs/01-data-analysis.md`](docs/01-data-analysis.md) and
[`docs/02-demo-cases.md`](docs/02-demo-cases.md) before writing code. The short version:

1. **On-time is recomputed, never read.** `is_ontime_15` on `mis.trips` is
   `actual_end_epoch − planned_end_epoch ≤ 15 min`. The `delay_reason` field claims
   90.2% on-time; the truth is 64.9%. `delay_minutes` is quarantined.

2. **Baselines must be same-weekday.** Fleet on-time is 96% on Sundays and 60% on
   Tuesdays. A naive trailing-28-day mean fires a false positive **every Sunday**.
   Compare Tuesdays to Tuesdays.

3. **Minimum sample is 40.** 58 of 84 raw July signals have n < 40 at a mean |z| of
   2.56 — loud and meaningless.

4. **An improvement can be a defect.** Santa Clara's on-time rose 16.5 points on
   19 July because planned duration rose 43% while actual journey time went 76.4 →
   77.0 min. `direction = 'better'` still needs investigating.

5. **There are no interventions in this data.** Vendor mix is frozen across all three
   months; no vendor–office pair starts or stops. The agent cannot learn "action X
   worked". It learns from whether its own **predictions** held up. Never claim a
   verified outcome for an action.

6. **Nulls carry meaning.** `signintype` null on 190,009 legs means "never picked up" —
   those legs are 62.1% no-show. Dropping them deletes the no-show signal.

7. **Currency is unstated.** `₹` is assumed from magnitude. Any headline money figure
   carries that caveat.

## 8. Every number must be sourced

The faithfulness gate is mechanical: it extracts every numeral from `narrative`,
`headline`, and `explanation` text and requires each to match a `value` in the
accompanying `evidence` array within rounding tolerance.

```json
"evidence": [
  {"claim": "on-time arrival", "value": 59.3, "unit": "%", "source": "mis.trips"},
  {"claim": "trailing same-weekday baseline", "value": 88.9, "unit": "%", "source": "entity_baseline"}
]
```

If you write "fell to 59.3% against an 88.9% baseline", both numbers must be in
`evidence`. **A number you cannot source, you do not write.** This is the answer to
the first question a judge will ask, so it is not negotiable.

## 9. Every incident carries four kinds of context

`incident.context` requires all four keys populated. The trace gate fails the incident
otherwise:

```json
{"trend":     {"statement": "...", "values": {}, "unit": "%"},
 "peer":      {"statement": "...", "peer_group": "...", "peer_median": 85.1, "pctile": 2},
 "threshold": {"statement": "...", "target": 80.0, "actual": 59.3, "unit": "%"},
 "impact":    {"statement": "...", "value": 398, "unit": "employees"}}
```

Impact must be in rupees, minutes, or people. Never a z-score in a sentence a human reads.

## 10. Getting started

```bash
make install     # backend deps
make build       # analytics/mis.duckdb from the CSVs — once, ~40s
make seed        # fixtures into every table, so nothing is empty
make replay      # runs whichever services exist
make api         # http://localhost:8000/docs
```

The dataset is not in the repo. Put `MoveInSync - Anonymised Trip-Log Dataset`
anywhere at or above the repo root, or set `MIS_DATA=/path/to/it`.

`make seed` is the unlock: it loads a realistic row into every table, so you can build
against real shapes before anyone else has written a line.
