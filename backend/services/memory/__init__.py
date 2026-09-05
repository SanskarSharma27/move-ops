"""Memory & evaluation. Records what the agent concluded, then checks whether it held.

This dataset contains no interventions, so the agent cannot learn "action X worked".
It learns from whether its own DIAGNOSES held up: every case carries a falsifiable
prediction with a date, and later replay days verify it against the raw data.
"""
from .pipeline import run_day  # noqa: F401
