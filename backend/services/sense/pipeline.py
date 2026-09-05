"""run_day - one business date through the sensing layer.

    field audit (once)  ->  baselines  ->  detectors  ->  counterfactual grid

Idempotent by construction: every id comes from `make_id` over business values, and
every write is an `upsert`, so running a day twice leaves the row counts unchanged.
"""
from __future__ import annotations

import logging
from datetime import date

from common import FIRST_DAY

from . import baselines, counterfactual, detectors, field_trust

log = logging.getLogger("sense")

_audited: set[int] = set()


def run_day(con, day: date) -> None:
    """Process one business date. Must be idempotent."""
    _audit_once(con, day)
    baselines.build_day(con, day)
    detectors.run(con, day)
    counterfactual.build_day(con, day)


def _audit_once(con, day: date) -> None:
    """The field audit is a property of the dataset, not of a day. Run it once.

    Keyed on the connection so a replay that starts mid-window still gets an audit -
    without it every detector would raise on the first `guard` call.
    """
    key = id(con)
    if key in _audited and day != FIRST_DAY:
        return
    field_trust.audit(con, day)
    _audited.add(key)
