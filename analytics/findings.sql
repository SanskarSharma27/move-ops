-- Every claim in docs/01-data-analysis.md, as a runnable query.
-- Format: each block starts with a marker line naming an ID and a title.
--   uv run analytics/mis.py findings
--   uv run analytics/mis.py findings F-04

-- @F-00 | Scope: what is actually in the dataset
select
  (select count(*) from trips)                          as trips,
  (select count(*) from emp_legs)                       as rider_legs,
  (select count(*) from bills)                          as bill_lines,
  (select count(*) from feedback)                       as feedback_rows,
  (select count(*) from alerts)                         as alerts,
  (select count(distinct business_unit) from trips)     as business_units,
  (select count(distinct office) from emp_legs)         as offices,
  (select count(distinct vendor) from trips)            as vendors,
  (select min(trip_date) from trips)                    as first_day,
  (select max(trip_date) from trips)                    as last_day;

-- @F-HOOK | The headline: reported punctuality vs. recomputed punctuality
select
  round(100.0 * count(*) filter (where delay_reason = 'NODELAY') / count(*), 1) as reported_ontime_pct,
  round(100.0 * count(*) filter (where is_ontime_15)             / count(*), 1) as actual_ontime_pct,
  round(corr(delay_minutes_reported, departure_delay_min), 3)                   as corr_with_departure_slip,
  round(corr(delay_minutes_reported, arrival_delay_min),   3)                   as corr_with_arrival_slip,
  round(100.0 * count(*) filter (where delay_minutes_reported = 0) / count(*), 1) as pct_reported_zero
from trips;

-- @F-TREND | Weekly on-time arrival: the V-shaped collapse and recovery
select date_trunc('week', trip_date)::date                     as week,
       count(*)                                                as trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 2) as ontime_pct,
       round(avg(arrival_delay_min), 1)                        as mean_delay_min
from trips group by 1 order by 1;

-- @F-CAUSE | Root cause of the June collapse: demand grew, fleet did not
select date_trunc('month', trip_date)::date                    as month,
       count(*)                                                as trips,
       count(distinct actual_cab_registration)                 as unique_cabs,
       round(count(*) * 1.0 / count(distinct actual_cab_registration), 1) as trips_per_cab,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1)  as ontime_pct
from trips where business_unit = 'pinnacle-Slc' group by 1 order by 1;

-- @F-01 | Alert acknowledgement: a 282x governance gap between business units
select business_unit, count(*) as alerts,
       round(avg(ack_minutes), 1)                              as mean_ack_min,
       round(quantile_cont(ack_minutes, 0.9), 1)               as p90_ack_min,
       count(*) filter (where acked_at is null)                as never_acked
from alerts group by 1 order by mean_ack_min desc;

-- @F-02 | 94% of "woman travelling alone" alerts fire with no escort aboard
select t.actual_escort, count(distinct a.trip_id) as trips_with_alert, count(*) as alerts
from alerts a join trips t using (trip_id)
where a.event_type = 'WOMAN_TRAVELLING_ALONE' group by 1 order by 1;

-- @F-03 | Panic answered in seconds, compliance alerts left for a day
select event_type, count(*) as alerts, count(distinct trip_id) as trips,
       round(avg(ack_minutes), 1)                              as mean_ack_min
from alerts group by 1 order by mean_ack_min desc;

-- @F-04 | 45% of spend is billed against zero recorded distance
select round(sum(trip_cost), 0)                                as total_spend,
       round(sum(trip_cost) filter (where is_zero_km), 0)      as zero_km_spend,
       round(100.0 * count(*) filter (where is_zero_km) / count(*), 2) as pct_lines_zero_km,
       round(100.0 * sum(trip_cost) filter (where is_zero_km) / sum(trip_cost), 1) as pct_spend_zero_km
from bills;

-- @F-04b | Vendors that bill zero-km on nearly every line
select vendor, count(*) as lines,
       round(100.0 * count(*) filter (where is_zero_km) / count(*), 1) as pct_zero_km,
       round(sum(trip_cost) filter (where is_zero_km), 0)      as zero_km_spend
from bills group by 1 having count(*) > 2000 order by pct_zero_km desc limit 12;

-- @F-05 | 6,999 trips carry duplicate billing lines
select count(distinct trip_id)                                 as trips_billed_twice,
       count(*)                                                as duplicate_lines,
       round(sum(trip_cost), 0)                                as spend_on_those_lines
from bills where trip_id in (
  select trip_id from bills where trip_id is not null group by 1 having count(*) > 1);

-- @F-06 | Contract arbitrage: identical seat class, 68% apart on cost per km
select contract, count(*) as lines, round(sum(trip_cost), 0) as spend,
       round(sum(trip_cost) / nullif(sum(billed_km), 0), 2)   as cost_per_km
from bills where billed_km > 0 group by 1 having count(*) > 3000 order by cost_per_km desc;

-- @F-07 | Solo riders in 4+ seat cabs, and fleet-wide seat utilisation
select t.business_unit, count(*) as solo_trips, round(sum(b.trip_cost), 0) as solo_spend
from trips t join bills b using (trip_id)
where t.actual_emp = 1 and t.cab_capacity >= 4
group by 1 order by solo_spend desc;

-- @F-07b | Seat utilisation and solo-ride share by business unit
select business_unit, count(*) as trips,
       round(avg(seat_util), 3)                                as mean_seat_util,
       round(100.0 * count(*) filter (where actual_emp = 1) / count(*), 1) as pct_solo
from trips group by 1 order by mean_seat_util;

-- @F-08 | Bill rows whose trip_id is the literal string 'OverHead'
select trip_id_raw, count(*) as lines, round(sum(trip_cost), 0) as spend
from bills where trip_id is null group by 1 order by lines desc;

-- @F-09 | The invisible vendor: worst-in-fleet, too small to appear on a dashboard
select vendor, count(*) as trips,
       round(100.0 * count(*) / (select count(*) from trips), 3) as pct_of_all_trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ontime_pct,
       round(avg(arrival_delay_min), 1)                        as mean_delay_min
from trips group by 1 having count(*) > 400 order by ontime_pct limit 10;

-- @F-10 | Vendor trend context: bad but improving changes the recommendation
select vendor, date_trunc('month', trip_date)::date as month, count(*) as trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ontime_pct
from trips where vendor in ('Vikram Mikhailov Travel', 'Pooja Sokolov Travel')
group by 1, 2 order by 1, 2;

-- @F-11 | Santa Clara is a sourcing problem, not a traffic problem
select vendor, count(*) as trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ontime_pct
from trips where office = 'Santa Clara Office' group by 1 order by trips desc;

-- @F-11b | Site scorecard: the peer spread every metric should be read against
select office, business_unit, count(*) as trips,
       round(100.0 * count(*) filter (where is_ontime_15) / count(*), 1) as ontime_pct,
       round(avg(seat_util), 2)                                as seat_util,
       round(100.0 * count(*) filter (where fuel_type = 'Electric') / count(*), 1) as ev_pct
from trips group by 1, 2 having count(*) > 5000 order by ontime_pct;

-- @F-12 | Two business units, 55% of distance, zero electrification
select business_unit, round(sum(traveled_km), 0) as total_km,
       round(100.0 * sum(traveled_km) filter (where fuel_type = 'Electric') / sum(traveled_km), 2) as ev_km_pct
from trips group by 1 order by ev_km_pct desc;

-- @F-12b | Fleet fuel mix barely moves across the quarter
with m as (
  select date_trunc('month', trip_date)::date as month, fuel_type, sum(traveled_km) as km
  from trips group by 1, 2)
select month, fuel_type, round(km, 0) as km,
       round(100.0 * km / sum(km) over (partition by month), 2) as pct
from m order by month, fuel_type;

-- @F-13 | No-shows: a 16x spread between offices, and a gender gap
select office, count(*) as legs,
       round(100.0 * count(*) filter (where is_no_show) / count(*), 2) as noshow_pct
from emp_legs group by 1 having count(*) > 20000 order by noshow_pct desc;

-- @F-13b | No-show by gender, and the signintype null that explains the spread
select coalesce(gender, '(null)') as gender, coalesce(signintype, '(null)') as signintype,
       count(*) as legs,
       round(100.0 * count(*) filter (where is_no_show) / count(*), 2) as noshow_pct
from emp_legs group by 1, 2 order by legs desc limit 12;

-- @F-14 | Rider-level lateness: the line manager's actual question
select round(100.0 * count(*) filter (where pickup_delay_min > 10) / count(*), 2) as pct_pickup_late_10min,
       round(quantile_cont(drop_delay_min, 0.5), 1)            as p50_drop_delay,
       round(quantile_cont(drop_delay_min, 0.9), 1)            as p90_drop_delay
from emp_legs where actual_pickup is not null;

-- @F-DEAD | Feedback has no discriminating signal: do not build a CSAT panel
select date_trunc('week', trip_at)::date as week, count(*) as responses,
       round(avg(driver_rating), 3) as driver, round(avg(safety_rating), 3) as safety,
       round(avg(route_rating), 3)  as route,  round(avg(cab_rating), 3)    as cab,
       round(100.0 * count(*) filter (where marshal_rating is null) / count(*), 1) as pct_marshal_unrated
from feedback group by 1 order by 1;

-- @F-MESS | Undocumented dirty values the data dictionary does not mention
select 'shift_type non-HH:MM (trips)' as quirk, shift_type as value, count(*) as n_rows
  from trips where shift_hour is null group by 1, 2
union all
select 'severity junk (alerts)', coalesce(severity_raw, '(null)'), count(*)
  from alerts where severity is null group by 1, 2
union all
select 'trip_id not numeric (bills)', trip_id_raw, count(*)
  from bills where trip_id is null group by 1, 2
union all
select 'negative km (emp_legs)', 'planned_km or traveled_km < 0', count(*)
  from emp_legs where km_invalid
order by n_rows desc;
