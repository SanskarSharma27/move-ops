"""Shared helpers. FROZEN — read this, import from it, never edit it.

Every service gets its database handle from here so there is exactly one place
that knows where the files live and how they are attached.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb

ROOT = Path(__file__).resolve().parent.parent
AGENT_DB = Path(__file__).resolve().parent / "agent.duckdb"
MIS_DB = ROOT / "analytics" / "mis.duckdb"
SCHEMA = ROOT / "contracts" / "schema.sql"
FIXTURES = ROOT / "contracts" / "fixtures"

FIRST_DAY = date(2026, 5, 1)
LAST_DAY = date(2026, 7, 31)
REPLAY_START = date(2026, 7, 1)   # memory warms up on May–June; July is what we score and show


def db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open agent.duckdb with the raw dataset attached read-only as `mis`.

    Tables therefore resolve as:  signal, incident, ...  (derived, writable)
                                  mis.trips, mis.alerts, ...  (raw, read-only)
    """
    con = duckdb.connect(str(AGENT_DB), read_only=read_only)
    if not MIS_DB.exists():
        raise FileNotFoundError(
            f"{MIS_DB} not found — the raw data is not built yet.\n"
            "Run:  make build     (about 40s, once)\n"
            "If the dataset lives elsewhere, set MIS_DATA=/path/to/"
            "'MoveInSync - Anonymised Trip-Log Dataset' first."
        )
    try:
        con.execute(f"attach '{MIS_DB}' as mis (read_only)")
    except duckdb.BinderException:
        pass  # already attached on this connection
    if not read_only:
        con.execute(SCHEMA.read_text())
    return con


def make_id(*parts: Any) -> str:
    """Deterministic 16-hex id from a natural key.

    Re-running a replay day MUST produce the same ids, so every write is an
    idempotent upsert rather than a duplicate. Always build ids from stable
    business values (date, entity, metric) — never from a timestamp or uuid4.
    """
    key = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def as_json(value: Any) -> str:
    """Serialise a dict/list for a `json` column, dates included."""
    return json.dumps(value, default=str, ensure_ascii=False)


def upsert(con, table: str, rows: Iterable[dict], key: str | tuple[str, ...]) -> int:
    """Delete-then-insert on the primary key. Safe to re-run for a day.

    Pass every column the table has; missing columns are inserted as NULL.
    """
    rows = list(rows)
    if not rows:
        return 0
    keys = (key,) if isinstance(key, str) else tuple(key)
    cols = list(rows[0].keys())
    for k in keys:
        if k not in cols:
            raise ValueError(f"upsert into {table}: key column {k!r} missing from row")
    placeholders = ", ".join("?" for _ in cols)
    where = " and ".join(f"{k} = ?" for k in keys)
    for row in rows:
        con.execute(f"delete from {table} where {where}", [row[k] for k in keys])
        con.execute(
            f"insert into {table} ({', '.join(cols)}) values ({placeholders})",
            [row[c] for c in cols],
        )
    return len(rows)


def now() -> datetime:
    return datetime.now()


def replay_days(start: date | None = None, end: date | None = None) -> list[date]:
    """Business dates for the replay, inclusive."""
    from datetime import timedelta
    start = start or FIRST_DAY
    end = end or LAST_DAY
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)]
