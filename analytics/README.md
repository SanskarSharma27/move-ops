# Analytics workbench

DuckDB over the raw CSVs. No install step beyond `uv`, which is already on this machine.

```bash
uv run analytics/mis.py build          # one-time, ~40s: builds analytics/mis.duckdb
uv run analytics/mis.py findings       # run all 28 named queries in findings.sql
uv run analytics/mis.py findings F-04  # run one
uv run analytics/mis.py "select business_unit, count(*) from trips group by 1"
uv run analytics/mis.py shell          # interactive REPL
```

`uv` reads the dependency block at the top of `mis.py` and fetches DuckDB itself.
If you move the dataset, set `MIS_DATA=/path/to/dataset`.

## Tables

`build` normalises the five raw files into five clean tables. Nothing is dropped —
bad values become `NULL` and are counted in the data-quality report the build prints.

| Table | Grain | Rows | Notable derived columns |
|---|---|---|---|
| `trips` | one cab trip | 615,546 | `arrival_delay_min`, `departure_delay_min`, `is_ontime_15`, `seat_util`, `shift_hour` |
| `emp_legs` | one rider's leg | 1,637,906 | `pickup_delay_min`, `drop_delay_min`, `km_invalid` |
| `alerts` | one safety/compliance event | 51,699 | `ack_minutes`, cleaned `severity` (+ `severity_raw`) |
| `bills` | one billed line item | 620,942 | `is_zero_km`, `trip_id_raw` |
| `feedback` | one rider rating | 512,873 | `marshal_rating` nulled where unrated |

All five join on `trip_id`. Coverage after normalisation: bills 99.1%, alerts 99.1%,
rider legs 100%, feedback 99.97%.

## Cleaning decisions

These are choices, not neutral fixes. Each one is defensible and each one is visible
in the table, so a reviewer can disagree with it.

| Problem | Decision | Why not the alternative |
|---|---|---|
| `trip_id` in three formats plus the literal `'OverHead'` | strip commas, `try_cast` to `bigint`; unparseable becomes `NULL` and keeps `trip_id_raw` | A strict cast aborts the whole 620k-row load on 160 bad rows |
| `delay_minutes` zero on 90.2% of trips | kept as `delay_minutes_reported`, never used for punctuality | It correlates 0.04 with actual departure slip — see `docs/01-data-analysis.md` |
| `severity` holds the string `'False'` | nulled, `severity_raw` retained | Silently mapping it to a real severity invents 15,037 incidents |
| `marshal_rating = 0` on 92.4% of rows | nulled | Averaging it in drags every marshal score toward zero |
| Negative `planned_km` / `traveled_km` (48 legs) | nulled, `km_invalid` flag set | Clipping to 0 hides that the row was ever wrong |
| `shift_type` = `'Non Shift'` / `'Adhoc'` | `shift_hour` is `NULL`, raw `shift_type` kept | Not in the data dictionary; any hour-parse crashes on these |
| `stwid = 0` | nulled | It is a placeholder for trip-level rows, not a person |
| `signintype` null on 190,009 legs | left null | Null here *means* "never picked up" — those legs are 62.1% no-show. Dropping them deletes the no-show signal |

## Adding a finding

Append to `findings.sql`. Blocks are split on a marker line:

```sql
-- @F-15 | One-line description of the claim
select ...;
```

Then `uv run analytics/mis.py findings F-15`. Keeping every claim as a runnable
query is what makes the numbers in `docs/` auditable rather than asserted.
