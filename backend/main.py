"""FastAPI app. FROZEN.

Routers are discovered, not registered. Drop a `router.py` into
backend/services/<name>/ that defines a module-level `router = APIRouter()` and it
is mounted at /api/<name>/ automatically. Nobody edits this file to add a service,
which is why four people can work in parallel without a merge conflict.
"""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from common import REPLAY_START, LAST_DAY, db, now  # noqa: E402

app = FastAPI(
    title="MoveOps",
    description="Agentic intelligence layer over enterprise commute operations.",
    version="1.0.0",
)

# The Angular dev server runs on 4200.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MOUNTED: list[str] = []
for path in sorted((BACKEND / "services").iterdir()):
    if not (path / "router.py").is_file():
        continue
    try:
        module = import_module(f"services.{path.name}.router")
        app.include_router(module.router, prefix=f"/api/{path.name}", tags=[path.name])
        MOUNTED.append(path.name)
    except Exception as exc:  # a broken service must not take down the others
        print(f"[main] could not mount services.{path.name}: {exc}", file=sys.stderr)


@app.get("/api/health")
def health():
    return {"status": "ok", "services": MOUNTED}


# ---------------------------------------------------------------- replay cursor
# The replay is precomputed by `make replay`; these endpoints move a cursor over
# the result so the demo never waits on computation. Owned by this file.

def _state():
    con = db(read_only=True)
    row = con.execute("select * from replay_state where id = 1").fetchone()
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row)) if row else None


def _set(**fields):
    # Read before opening the write handle: DuckDB will not give one process a
    # read-only and a read-write connection to the same file at the same time.
    cur = _state() or {
        "id": 1, "current_day": REPLAY_START, "first_day": REPLAY_START,
        "last_day": LAST_DAY, "status": "idle", "days_done": 0, "trips_seen": 0,
        "signals_raised": 0, "suppressed": 0, "incidents_open": 0,
    }
    con = db()
    cur.update(fields)
    cur["updated_at"] = now()
    cols = ", ".join(cur)
    con.execute("delete from replay_state where id = 1")
    con.execute(f"insert into replay_state ({cols}) values ({', '.join('?' * len(cur))})",
                list(cur.values()))
    con.close()
    return cur


@app.get("/api/replay/state")
def replay_state():
    state = _state()
    if state is None:
        state = _set()
    return state


@app.post("/api/replay/play")
def replay_play():
    return _set(status="running")


@app.post("/api/replay/pause")
def replay_pause():
    return _set(status="paused")


@app.post("/api/replay/reset")
def replay_reset():
    return _set(current_day=REPLAY_START, status="idle", days_done=0)


@app.post("/api/replay/seek")
def replay_seek(day: str):
    from datetime import date as _date
    try:
        target = _date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "day must be ISO format, e.g. 2026-07-21")
    if not (REPLAY_START <= target <= LAST_DAY):
        raise HTTPException(400, f"day must fall between {REPLAY_START} and {LAST_DAY}")
    return _set(current_day=target, status="paused")
