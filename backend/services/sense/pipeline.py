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

# Connections already audited in this process. The connection object is the *value*, not
# just its id, so a closed connection cannot have its address reused by a later one and
# silently skip the audit.
_audited: dict[int, object] = {}


def run_day(con, day: date) -> None:
    """Process one business date. Must be idempotent."""
    _audit_once(con, day)
    baselines.build_day(con, day)
    detectors.run(con, day)
    counterfactual.build_day(con, day)


def _audit_once(con, day: date) -> None:
    """The field audit is a property of the dataset, not of a day. Run it once.

    Keyed on the connection rather than on the date so a replay that starts mid-window
    still gets an audit. Without one, the first `guard` call would find an empty
    field_trust table, and every detector would run against columns nothing had cleared.
    """
    if id(con) in _audited and day != FIRST_DAY:
        return
    field_trust.audit(con, day)
    _audited[id(con)] = con
