"""The replay driver. FROZEN.

Steps day by day through the dataset and hands each business date to every service
that exists. A service participates by exporting `run_day(con, day)` from its
package __init__.py. A service that does not exist yet is skipped with a note —
which is why B can build before A is finished.

    make replay                      # full window, May 1 -> Jul 31
    python backend/replay.py --from 2026-07-01
    python backend/replay.py --only sense
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from importlib import import_module
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from common import FIRST_DAY, LAST_DAY, REPLAY_START, db, now, replay_days  # noqa: E402

# Order matters: sense produces signals, reason consumes them, memory consumes incidents.
PIPELINE = ("sense", "reason", "memory")


def load(name: str):
    path = BACKEND / "services" / name
    if not (path / "__init__.py").is_file():
        return None
    try:
        return getattr(import_module(f"services.{name}"), "run_day", None)
    except Exception as exc:
        print(f"[replay] services.{name} failed to import: {exc}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=str(FIRST_DAY))
    ap.add_argument("--to", dest="end", default=str(LAST_DAY))
    ap.add_argument("--only", nargs="*", default=list(PIPELINE))
    args = ap.parse_args()

    stages = [(n, load(n)) for n in PIPELINE if n in args.only]
    live = [(n, f) for n, f in stages if f]
    for name, fn in stages:
        if not fn:
            print(f"[replay] skipping {name} — not implemented yet")
    if not live:
        print("[replay] no services available yet — nothing to do.")
        print("[replay] this is expected until the first service lands. Use `make seed` meanwhile.")
        return 0
    print(f"[replay] running: {', '.join(n for n, _ in live)}")

    con = db()
    days = replay_days(date.fromisoformat(args.start), date.fromisoformat(args.end))
    for i, day in enumerate(days, 1):
        for name, fn in live:
            try:
                fn(con, day)
            except Exception:
                print(f"[replay] {name} raised on {day}:", file=sys.stderr)
                traceback.print_exc()
        if i % 10 == 0 or day == days[-1]:
            print(f"[replay] {day}  ({i}/{len(days)})")

    counts = {
        t: con.execute(f"select count(*) from {t}").fetchone()[0]
        for t in ("signal", "incident", "suppression", "case_file", "prediction")
    }
    trips = con.execute(
        "select count(*) from mis.trips where trip_date between ? and ?",
        [days[0], days[-1]],
    ).fetchone()[0]

    con.execute("delete from replay_state where id = 1")
    con.execute(
        """insert into replay_state (id, current_day, first_day, last_day, status,
             days_done, trips_seen, signals_raised, suppressed, incidents_open, updated_at)
           values (1, ?, ?, ?, 'done', ?, ?, ?, ?, ?, ?)""",
        [REPLAY_START, REPLAY_START, days[-1], len(days), trips,
         counts["signal"], counts["suppression"], counts["incident"], now()],
    )
    con.close()

    print(f"\n[replay] done — {len(days)} days, {trips:,} trips")
    for k, v in counts.items():
        print(f"           {k:<12} {v:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
