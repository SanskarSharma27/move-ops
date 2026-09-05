# MoveOps — one entry point for everything.
# Nobody edits this file. If you need a target, ask the repo owner.

VENV := .venv
PY   := $(VENV)/bin/python
UV   := uv

.PHONY: help install build seed replay api ui test clean

help:
	@echo "make install   install backend deps (root + every service)"
	@echo "make build     build analytics/mis.duckdb from the raw CSVs (~40s, once)"
	@echo "make seed      load contracts/fixtures into backend/agent.duckdb"
	@echo "make replay    run the 92-day replay through every service present"
	@echo "make api       serve the API on http://localhost:8000 (docs at /docs)"
	@echo "make ui        run the Angular dev server on http://localhost:4200"
	@echo "make test      run every service's tests"

install:
	$(UV) venv $(VENV)
	$(UV) pip install --python $(PY) -r backend/requirements.txt
	@for f in backend/services/*/requirements.txt; do \
	  if [ -f "$$f" ]; then echo "installing $$f"; $(UV) pip install --python $(PY) -r "$$f"; fi; \
	done

build:
	$(UV) run analytics/mis.py build

seed:
	$(PY) backend/seed.py

replay:
	$(PY) backend/replay.py

api:
	$(VENV)/bin/uvicorn main:app --app-dir backend --reload --port 8000

ui:
	cd frontend && npm start

test:
	$(VENV)/bin/pytest backend/services -q

clean:
	rm -f backend/agent.duckdb backend/agent.duckdb.wal
