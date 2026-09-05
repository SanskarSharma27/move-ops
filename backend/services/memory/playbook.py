"""Procedural memory. An entry is created ONLY by promotion, never by assertion.

The rule is two confirmed predictions for the same detector and grain. Nothing
writes this table directly, which is what makes the playbook filling up during
the replay a real result rather than a decoration.
"""
from __future__ import annotations

import datetime as dt

from common import as_json, make_id, upsert

MIN_CONFIRMED = 2

# The procedural lesson each detector teaches once its diagnoses start holding up.
DETECTOR_ACTION = {
    "punctuality_drop": "Check whether every vendor at the site degraded together before attributing the drop to any one vendor, then compare actual minutes per km against planned minutes per km before recommending a schedule change.",
    "metric_integrity": "Before accepting an improvement, recompute the underlying quantity. Confirm actual journey time, alert mix and denominator are unchanged; a target that moved is not a result.",
    "alert_ack_sla": "Benchmark acknowledgement against the fastest unit on the same platform before proposing headcount. The gap is usually process.",
    "safety_cluster": "Pair severity volume with acknowledgement time. The unit raising the most severe alerts is not necessarily the one answering them.",
    "noshow_spike": "Check signintype completeness before ranking offices. A null there means the leg was never picked up, so the spread can be a recording difference.",
    "escort_breach": "Count the trips where the control was absent, not the alerts raised, so one trip with several alerts is not read as several breaches.",
    "billing_anomaly": "Separate zero-distance lines from duplicates before quoting recoverable money. Fixed-slab billing and telemetry failure look identical in this data.",
    "vendor_chronic": "Read the trend before the level. A vendor that is bad and improving and one that is bad and flat take opposite actions.",
}

# One sentence of the same lesson, for the case diagnosis a manager reads.
DETECTOR_LESSON = {
    "punctuality_drop": "All vendors at the site move together on the affected days, so this is a site-level scheduling problem rather than a vendor failure.",
    "metric_integrity": "The level moved but the underlying journey did not, so the gain belongs to the schedule and not to the operation.",
    "alert_ack_sla": "Other units answer the same alert types on the same platform in minutes, so the gap is process rather than volume.",
    "safety_cluster": "The unit generating the most severe safety events is also the slowest to answer them, which makes this a staffing and policy finding.",
    "noshow_spike": "Check whether the legs were ever picked up before reading this as employee behaviour.",
    "escort_breach": "The alert exists to catch this condition and the control that should answer it is usually absent when it fires.",
    "billing_anomaly": "Nothing in the data distinguishes a fixed-slab contract from a telemetry failure, and that ambiguity is itself the finding.",
    "vendor_chronic": "The level alone does not decide the action here; the trend does.",
}


def promote(con, day: dt.date) -> int:
    """Recompute every entry that has earned its place, as of `day`.

    `n_cases` is the denominator confidence is measured over: predictions that
    actually resolved. Unverifiable ones are excluded rather than counted as losses.
    """
    rows = con.execute(
        """select c.detector_sig, c.case_id, p.prediction_id, p.outcome, p.verified_on
           from prediction p
           join (select case_id,
                        split_part(signature, '|', 1) || '|' || split_part(signature, '|', 2) as detector_sig
                 from case_file) c using (case_id)
           where p.outcome in ('confirmed', 'refuted') and p.verified_on <= ?
           order by p.verified_on, p.prediction_id""", [day]).fetchall()

    grouped: dict[str, list] = {}
    for sig, case_id, pid, outcome, verified_on in rows:
        grouped.setdefault(sig, []).append((case_id, pid, outcome, verified_on))

    out = []
    for sig, entries in grouped.items():
        confirmed = [e for e in entries if e[2] == "confirmed"]
        if len(confirmed) < MIN_CONFIRMED:
            continue
        detector = sig.split("|", 1)[0]
        action = DETECTOR_ACTION.get(detector)
        if not action:
            continue
        out.append({
            "playbook_id": make_id("playbook", sig, action),
            "signature": sig, "action": action,
            "n_cases": len(entries), "n_confirmed": len(confirmed),
            "confidence": round(len(confirmed) / len(entries), 3),
            "evidence": as_json({
                "case_ids": sorted({e[0] for e in entries}),
                "prediction_ids": [e[1] for e in confirmed],
                "note": f"Promoted after {len(confirmed)} confirmed predictions on this "
                        f"detector and grain; {len(entries) - len(confirmed)} refuted.",
            }),
            # Stable across re-runs: the day the second confirmation actually landed.
            "promoted_on": confirmed[MIN_CONFIRMED - 1][3],
            "updated_on": day,
        })
    live = [r["playbook_id"] for r in out] or [""]
    con.execute(f"delete from playbook where playbook_id not in ({','.join('?' * len(live))})", live)
    return upsert(con, "playbook", out, key="playbook_id")
