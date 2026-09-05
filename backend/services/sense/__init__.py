"""sense — the layer that watches the data and decides what is worth mentioning.

Writes four tables: field_trust, entity_baseline, signal, counterfactual.
Reads mis.* and nothing else. Never writes another service's tables.
"""
from .pipeline import run_day  # noqa: F401

__all__ = ["run_day"]
