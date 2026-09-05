# MoveOps

An agentic intelligence layer over enterprise commute data — it senses what is
happening, reasons about what it means, and acts, for the transport manager,
the transport & facilities head, and the line manager.

Built for the MoveInSync hackathon on a 92-day slice of real operations data
(615,546 trips, 1.6M rider legs, May–July 2026).

## What makes it different

Most tools report the fields they are given. This one **audits whether the numbers
are telling the truth**, and it has three provable cases in this dataset:

- The platform reports 90.2% on-time. Recomputed from timestamps it is **64.9%**.
- Santa Clara's punctuality jumped 16.5 points on July 19 — because planned duration
  rose 43% while **actual journey time did not change**.
- pinnacle-Slc's alert response "improved" 2.8× — because they **stopped generating**
  the alert type that was slow. `DEVICE_NOT_REACHABLE` sits at 1,444.9 minutes,
  unchanged for three months.

And it explains what it *ignores*: 84 raw signals in July become **10 incidents**,
each of the 74 suppressions carrying a stated reason.

## Quick start

```bash
make install     # backend deps
make build       # build analytics/mis.duckdb from the CSVs (once, ~40s)
make seed        # load fixtures so every surface has data immediately
make replay      # run the replay through whichever services exist
make api         # http://localhost:8000/docs
make ui          # http://localhost:4200
```

The raw dataset is **not** in this repo. Put the `MoveInSync - Anonymised Trip-Log
Dataset` folder anywhere at or above the repo root and `make build` will find it,
or set `MIS_DATA=/path/to/dataset`.

## Layout

| Path | Owner | What |
|---|---|---|
| `contracts/` | frozen | schema, API spec, fixtures — the interfaces |
| `backend/main.py` `replay.py` `common.py` `seed.py` | frozen | infrastructure |
| `backend/services/sense/` | A | field trust, baselines, detectors, counterfactuals |
| `backend/services/reason/` | B | correlation, suppression, hypotheses, narrative |
| `backend/services/memory/` | C | cases, predictions, playbooks, evaluation |
| `frontend/` | D | Angular demo surface |
| `analytics/` | shared, read-only | DuckDB workbench over the raw CSVs |
| `docs/` | shared, read-only | data analysis and demo-case research |
| `prds/` | shared, read-only | one build brief per workstream |

`CONTRACT.md` is the single source of truth for anything crossing a folder boundary.

## Rules

1. You edit only your own folder. Nothing else, ever.
2. Frozen files are frozen. If one is wrong, report it — do not edit it.
3. Commit every 30–45 minutes with `git add <your folder>`. Never `git add .`.
