# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0"]
# ///
"""MoveInSync hackathon analytics.

    uv run analytics/mis.py build            # one-time: build mis.duckdb from the raw CSVs
    uv run analytics/mis.py findings         # run every query in findings.sql
    uv run analytics/mis.py findings F-04    # run one finding
    uv run analytics/mis.py "select ..."     # ad-hoc SQL
    uv run analytics/mis.py shell            # interactive REPL

Point MIS_DATA at the dataset folder if you move it.
"""
import os, sys, textwrap
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent
DB = ROOT / "mis.duckdb"
def _find_data():
    """Walk up from here looking for the dataset folder, so the repo can live anywhere."""
    for base in [ROOT, *list(ROOT.parents)[:4]]:
        for c in base.glob("**/MoveInSync - Anonymised Trip-Log Dataset"):
            if (c / "bill_data.csv").is_file():
                return c
        if base.name == "/":
            break
    return ROOT.parent / "MoveInSync - Anonymised Trip-Log Dataset"

DATA = Path(os.environ["MIS_DATA"]) if os.environ.get("MIS_DATA") else _find_data()

# ---------------------------------------------------------------- output

def show(rel, limit=200):
    cols = [d[0] for d in rel.description]
    rows = rel.fetchmany(limit)
    if not rows:
        print("  (no rows)"); return
    def fmt(v):
        if v is None: return "-"
        if isinstance(v, float): return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e12 else f"{v:.3g}"
        if isinstance(v, int): return f"{v:,}"
        return str(v)
    table = [cols] + [[fmt(v) for v in r] for r in rows]
    w = [max(len(r[i]) for r in table) for i in range(len(cols))]
    print("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * w[i] for i in range(len(cols))))
    for r in table[1:]:
        print("  " + "  ".join(v.rjust(w[i]) if v.replace(",","").replace(".","").replace("-","").isdigit()
                               else v.ljust(w[i]) for i, v in enumerate(r)))
    if len(rows) == limit:
        print(f"  ... truncated at {limit} rows")

# ---------------------------------------------------------------- build
# Every cast below is deliberately tolerant. try_cast yields NULL instead of
# raising, so a single bad value (bill_data.trip_id = 'OverHead') cannot abort
# a 620k-row load. What gets nulled is counted in the data-quality report.

BUILD = f"""
create or replace table trips as
with raw as (
  select
    business_unit, office, product_type,
    strptime(trip_date, '%B %-d, %Y')::date                       as trip_date,
    shift_type,
    try_cast(split_part(shift_type, ':', 1) as int)               as shift_hour,
    try_cast(replace(trip_id, ',', '') as bigint)                 as trip_id,
    trip_direction,
    lower(actual_escort) = 'true'                                 as actual_escort,
    vendor_id                                                     as vendor,
    planned_cab_registration, actual_cab_registration,
    try_cast(actual_cab_capacity as int)                          as cab_capacity,
    case when try_cast(replace(planned_km, ',', '') as double) < 0 then null
         else try_cast(replace(planned_km, ',', '') as double) end as planned_km,
    case when try_cast(replace(traveled_km, ',', '') as double) < 0 then null
         else try_cast(replace(traveled_km, ',', '') as double) end as traveled_km,
    try_cast(replace(planned_start_epoch, ',', '') as bigint)     as planned_start,
    try_cast(replace(planned_end_epoch, ',', '') as bigint)       as planned_end,
    try_cast(replace(actual_start_epoch, ',', '') as bigint)      as actual_start,
    try_cast(replace(actual_end_epoch, ',', '') as bigint)        as actual_end,
    delay_reason,
    try_cast(replace(delay_minutes, ',', '') as double)           as delay_minutes_reported,
    route_source,
    actual_cab_fuel_type                                          as fuel_type,
    lower(is_driver_nc) = 'true'                                  as is_driver_nc,
    lower(is_cab_nc)    = 'true'                                  as is_cab_nc,
    nullif(trip_nodal, 'NA')                                      as trip_nodal,
    try_cast(plannedemployee_cnt as int)                          as planned_emp,
    try_cast(actualemployee_cnt as int)                           as actual_emp,
    try_cast(noshow_cnt as int)                                   as noshow_cnt
  from read_csv_auto('{DATA}/Ride_data _trip-*.csv', union_by_name=true, all_varchar=true)
)
select *,
  (actual_end   - planned_end)   / 60.0                           as arrival_delay_min,
  (actual_start - planned_start) / 60.0                           as departure_delay_min,
  (actual_end   - planned_end)   / 60.0 <= 15                     as is_ontime_15,
  case when cab_capacity > 0 then actual_emp * 1.0 / cab_capacity end as seat_util
from raw;

create or replace table emp_legs as
with raw as (
  select
    business_unit, office, product_type,
    try_cast(trip_date as date)                                   as trip_date,
    shift_type,
    try_cast(split_part(shift_type, ':', 1) as int)               as shift_hour,
    trip_id,
    planned_pickup_epoch as planned_pickup, planned_drop_epoch as planned_drop,
    actual_pickup_epoch  as actual_pickup,  actual_drop_epoch  as actual_drop,
    case when planned_km  < 0 then null else planned_km  end      as planned_km,
    case when traveled_km < 0 then null else traveled_km end      as traveled_km,
    coalesce(planned_km < 0 or traveled_km < 0, false)            as km_invalid,
    nullif(stwid, 0)                                              as stwid,
    signintype, gender, emp_role, boarding_status, not_boarding_reason, is_no_show
  from read_csv_auto('{DATA}/emp_Data.csv')
)
select *,
  (actual_pickup - planned_pickup) / 60.0                         as pickup_delay_min,
  (actual_drop   - planned_drop)   / 60.0                         as drop_delay_min
from raw;

create or replace table alerts as
with raw as (
  select
    business_unit,
    try_cast(replace(trip_id, ',', '') as bigint)                 as trip_id,
    nullif(try_cast(replace(stwid, ',', '') as bigint), 0)        as stwid,
    event_id, event_type,
    strptime(start_time,       '%B %-d, %Y, %-I:%M %p')           as raised_at,
    strptime(acknowledge_time, '%B %-d, %Y, %-I:%M %p')           as acked_at,
    state_text,
    case when severity in ('Sev-1','Sev-2','Sev-3') then severity end as severity,
    severity                                                      as severity_raw,
    source
  from read_csv_auto('{DATA}/alerts_data.csv', all_varchar=true)
)
select *, date_diff('minute', raised_at, acked_at) as ack_minutes from raw;

create or replace table bills as
select
  business_unit, office, vendor,
  strptime(cycle_start, '%B %-d, %Y, %-I:%M %p')::date            as cycle_start,
  strptime(cycle_end,   '%B %-d, %Y, %-I:%M %p')::date            as cycle_end,
  try_cast(replace(trip_id, ',', '') as bigint)                   as trip_id,
  trip_id                                                         as trip_id_raw,
  contract, slab_name,
  try_cast(total_trip_km as double)                               as billed_km,
  try_cast(replace(trip_cost, ',', '') as double)                 as trip_cost,
  try_cast(total_trip_km as double) = 0                           as is_zero_km
from read_csv_auto('{DATA}/bill_data.csv', all_varchar=true);

create or replace table feedback as
select
  business_unit,
  try_cast(replace(trip_id, ',', '') as bigint)                   as trip_id,
  trip_type,
  strptime(trip_date, '%B %-d, %Y, %-I:%M %p')                    as trip_at,
  nullif(try_cast(replace(stwid, ',', '') as bigint), 0)          as stwid,
  try_cast(route_rating  as int)                                  as route_rating,
  try_cast(driver_rating as int)                                  as driver_rating,
  try_cast(cab_rating    as int)                                  as cab_rating,
  try_cast(safety_rating as int)                                  as safety_rating,
  -- 92.4% of marshal_rating is 0 with no marshal on the trip: unrated, not a low score.
  nullif(try_cast(marshal_rating as int), 0)                      as marshal_rating,
  strptime(creation_time, '%B %-d, %Y, %-I:%M %p')                as submitted_at
from read_csv_auto('{DATA}/trip_feedback.csv', all_varchar=true);
"""

DQ = """
select 'trips'    as tbl, count(*) n_rows, sum(case when trip_id is null then 1 else 0 end) bad_trip_id,
       sum(case when shift_hour is null then 1 else 0 end) unparseable_shift,
       sum(case when arrival_delay_min is null then 1 else 0 end) missing_timing from trips
union all select 'emp_legs', count(*), sum(case when trip_id is null then 1 else 0 end),
       sum(case when shift_hour is null then 1 else 0 end), sum(case when km_invalid then 1 else 0 end) from emp_legs
union all select 'alerts', count(*), sum(case when trip_id is null then 1 else 0 end),
       sum(case when severity is null then 1 else 0 end), sum(case when acked_at is null then 1 else 0 end) from alerts
union all select 'bills', count(*), sum(case when trip_id is null then 1 else 0 end),
       sum(case when slab_name is null then 1 else 0 end), sum(case when is_zero_km then 1 else 0 end) from bills
union all select 'feedback', count(*), sum(case when trip_id is null then 1 else 0 end),
       sum(case when marshal_rating is null then 1 else 0 end), 0 from feedback
"""

JOINS = """
select 'bills -> trips' as edge,
  round(100.0 * count(*) filter (where t.trip_id is not null) / count(*), 2) as pct_matched
from bills b left join trips t using (trip_id) where b.trip_id is not null
union all select 'alerts -> trips',
  round(100.0 * count(*) filter (where t.trip_id is not null) / count(*), 2)
from alerts a left join trips t using (trip_id) where a.trip_id is not null
union all select 'emp_legs -> trips',
  round(100.0 * count(*) filter (where t.trip_id is not null) / count(*), 2)
from emp_legs e left join trips t using (trip_id)
union all select 'feedback -> trips',
  round(100.0 * count(*) filter (where t.trip_id is not null) / count(*), 2)
from feedback f left join trips t using (trip_id) where f.trip_id is not null
"""

def build():
    if not DATA.is_dir():
        sys.exit(f"Dataset folder not found: {DATA}\nSet MIS_DATA=/path/to/dataset")
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    print(f"Building {DB.name} from {DATA.name} ...")
    for stmt in filter(str.strip, BUILD.split(";\n")):
        name = stmt.split("table", 1)[1].split("as", 1)[0].strip() if "table" in stmt else "?"
        print(f"  loading {name} ...", flush=True)
        con.execute(stmt)
    print("\nData quality after cleaning (nulled, not dropped):")
    show(con.execute(DQ))
    print("\nJoin coverage on the normalised key:")
    show(con.execute(JOINS))
    con.close()
    print(f"\nDone -> {DB}")

# ---------------------------------------------------------------- findings

def parse_findings():
    """findings.sql is split on lines beginning `-- @`: `-- @ID | title`."""
    text = (ROOT / "findings.sql").read_text()
    out = []
    for block in text.split("-- @")[1:]:
        head, _, body = block.partition("\n")
        fid, _, title = head.partition("|")
        out.append((fid.strip(), title.strip(), body.strip().rstrip(";")))
    return out

def run_findings(only=None):
    con = connect()
    for fid, title, sql in parse_findings():
        if only and only.upper() not in fid.upper():
            continue
        print(f"\n{'='*78}\n{fid}  {title}\n{'='*78}")
        try:
            show(con.execute(sql))
        except Exception as e:
            print(f"  ERROR: {e}")

def connect():
    if not DB.exists():
        sys.exit("mis.duckdb not found. Run:  uv run analytics/mis.py build")
    return duckdb.connect(str(DB), read_only=True)

# ---------------------------------------------------------------- entry

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "help"
    if arg == "build":
        build()
    elif arg == "findings":
        run_findings(sys.argv[2] if len(sys.argv) > 2 else None)
    elif arg == "shell":
        con = connect()
        print("Tables: trips, emp_legs, alerts, bills, feedback.  Ctrl-D to exit.")
        while True:
            try:
                q = input("mis> ").strip()
            except EOFError:
                print(); break
            if not q: continue
            try: show(con.execute(q))
            except Exception as e: print(f"  ERROR: {e}")
    elif arg in ("help", "-h", "--help"):
        print(textwrap.dedent(__doc__))
    else:
        show(connect().execute(" ".join(sys.argv[1:])))

if __name__ == "__main__":
    main()
