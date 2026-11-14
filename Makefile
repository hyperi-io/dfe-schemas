# Project:   dfe-schemas
# File:      Makefile
# Purpose:   CI targets wrapping hyperi-ci, plus schema validation
#
# License:   BUSL-1.1
# Copyright: (c) 2026 HYPERI PTY LIMITED
#
# `validate` needs dfe-engine importable: PY=../dfe-engine/.venv/bin/python make validate

PY ?= uv run python

.PHONY: quality test build check render validate

quality:
	hyperi-ci run quality

test:
	hyperi-ci run test

build:
	hyperi-ci run build

check:
	hyperi-ci check

render:
	$(PY) scripts/render_manifest.py

validate:
	$(PY) scripts/validate_schemas.py
