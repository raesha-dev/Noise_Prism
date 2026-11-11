.DEFAULT_GOAL := help
.PHONY: help setup test lint e2e dev dev-mock preflight plan

ifeq ($(OS),Windows_NT)
PYTHON ?= py -3
VENV_PYTHON = .venv/Scripts/python.exe
NPM = npm.cmd
else
PYTHON ?= python3
VENV_PYTHON = .venv/bin/python
NPM = npm
endif

MANIFEST ?=
CANDIDATES ?= C0
MODELS ?= v2.5
RUNS ?= 1

help:
	@echo "setup test lint e2e dev dev-mock preflight plan"

setup:
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install -r bharatnoise_api/requirements-api.txt
	$(NPM) install

test:
	$(VENV_PYTHON) -m pytest -q
	$(NPM) test

lint:
	$(NPM) run lint
	$(NPM) exec tsc -- --noEmit

e2e:
	$(VENV_PYTHON) bharatnoise_api/scripts/e2e_smoke.py
	$(NPM) run test:e2e

dev:
	$(VENV_PYTHON) scripts/dev.py

dev-mock:
	$(VENV_PYTHON) scripts/dev.py --mock

preflight:
	$(VENV_PYTHON) bharatnoise_api/scripts/preflight_gnani.py

ifeq ($(strip $(MANIFEST)),)
plan:
	@echo "Set MANIFEST to a CSV manifest, for example MANIFEST=data/eval_dev/dev.csv." >&2
	@exit 2
else
plan:
	$(VENV_PYTHON) bharatnoise_api/scripts/plan_budget.py --manifest "$(MANIFEST)" --candidates $(CANDIDATES) --models $(MODELS) --runs $(RUNS)
endif
