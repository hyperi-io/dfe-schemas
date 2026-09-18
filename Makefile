# Project:   dfe-schemas
# File:      Makefile
# Purpose:   CI targets wrapping hyperi-ci, plus schema validation
#
# License:   BUSL-1.1
# Copyright: (c) 2026 HYPERI PTY LIMITED
#
# `render` needs nothing but this package -- it is how the source of truth
# checks its own content. `validate` still needs dfe-engine importable for the
# meta-schema models: set PY to an interpreter that has it, e.g.
# PY=../dfe-engine/.venv/bin/python, or run inside such a venv.

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
