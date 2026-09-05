"""Load contracts/fixtures into agent.duckdb. FROZEN.

Run this first, before any service exists. It puts a realistic row in every table
so the Angular app and the downstream services have something to build against
from minute one.

    make seed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from common import FIXTURES, LAST_DAY, REPLAY_START, db, now, upsert  # noqa: E402

TABLES = {
    "field_trust": ("table_name", "column_name"),
    "entity_baseline": ("as_of", "entity_type", "entity_id", "metric"),
    "signal": "signal_id",
    "counterfactual": ("as_of", "entity_type", "entity_id", "lever", "param", "metric"),
    "incident": "incident_id",
    "suppression": "suppression_id",
    "hypothesis": "hypothesis_id",
    "case_file": "case_id",
    "prediction": "prediction_id",
    "playbook": "playbook_id",
    "eval_result": ("run_id", "gate", "metric"),
}


def main() -> int:
    con = db()
    total = 0
    for table, key in TABLES.items():
        path = FIXTURES / f"{table}.json"
        if not path.is_file():
            print(f"  {table:<18} no fixture file, skipped")
            continue
        rows = json.loads(path.read_text())
        for row in rows:
            for col, val in list(row.items()):
                if isinstance(val, (dict, list)):
                    row[col] = json.dumps(val, ensure_ascii=False)
        n = upsert(con, table, rows, key)
        total += n
        print(f"  {table:<18} {n} rows")

    con.execute("delete from replay_state where id = 1")
    con.execute(
        """insert into replay_state (id, current_day, first_day, last_day, status,
             days_done, trips_seen, signals_raised, suppressed, incidents_open, updated_at)
           values (1, ?, ?, ?, 'idle', 0, 0, ?, ?, ?, ?)""",
        [REPLAY_START, REPLAY_START, LAST_DAY,
         con.execute("select count(*) from signal").fetchone()[0],
         con.execute("select count(*) from suppression").fetchone()[0],
         con.execute("select count(*) from incident").fetchone()[0],
         now()],
    )
    con.close()
    print(f"\nseeded {total} rows into backend/agent.duckdb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
