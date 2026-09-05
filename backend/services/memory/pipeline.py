"""One replay day of memory. Idempotent by construction: nothing here increments,
everything is recomputed from the evidence that exists on or before `day`.
"""
from __future__ import annotations

import datetime as dt

from common import LAST_DAY

from . import cases, evaluate, playbook, verify

EVAL_EVERY_DAYS = 7   # the report card needs to move during the demo, not only at month end


def is_eval_day(day: dt.date) -> bool:
    return day == LAST_DAY or day.day % EVAL_EVERY_DAYS == 0


def run_day(con, day: dt.date) -> None:
    verify.verify_due(con, day)              # was yesterday's diagnosis right?
    open_cases = cases.collect(con, day)     # what do we know as of today?
    verify.ensure_pending(con, day, open_cases)   # every live case carries a claim
    cases.write(con, day, open_cases)
    playbook.promote(con, day)               # only confirmed predictions earn an entry
    if is_eval_day(day):
        evaluate.run(con, day)
