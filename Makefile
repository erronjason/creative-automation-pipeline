# Convenience targets. Every target is a thin wrapper; see README for the raw commands.
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup demo serve test lint showcase clean

setup:            ## create a virtualenv and install the project + dev tools
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "ready: run 'make demo' (offline) or 'make serve' (web UI)"

demo:             ## run both example briefs offline with the mock provider
	$(BIN)/cap demo

serve:            ## local web UI at http://127.0.0.1:8765
	$(BIN)/cap serve

test:             ## full test suite (hermetic: no network, no keys)
	$(BIN)/pytest

lint:
	$(BIN)/ruff check src tests && $(BIN)/ruff format --check src tests

showcase:         ## rebuild the static GitHub Pages showcase from ./output
	$(BIN)/cap showcase output/summer-refresh-2026 output/afternoon-boost-demo --dest showcase

clean:
	rm -rf output .cache .pytest_cache .ruff_cache
