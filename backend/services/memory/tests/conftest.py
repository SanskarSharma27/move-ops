"""Import paths only. `make test` runs pytest from the repo root, where neither
backend/ nor backend/services/ is on the path — replay.py and main.py add them
at runtime, so the tests do the same rather than depending on how pytest is invoked.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
for path in (BACKEND, BACKEND / "services"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
