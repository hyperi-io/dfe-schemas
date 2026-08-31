# Project:   dfe-schemas
# File:      Makefile
# Purpose:   CI targets wrapping hyperi-ci, plus schema validation
#
# License:   BUSL-1.1
# Copyright: (c) 2026 HYPERI PTY LIMITED
#
# `validate` needs dfe-engine importable. Set PY to an interpreter that has it,
# e.g. PY=../dfe-engine/.venv/bin/python, or run inside such a venv.

PY ?= python

.PHONY: quality test build check validate

quality:
	hyperi-ci run quality

test:
	hyperi-ci run test

build:
	hyperi-ci run build

check:
	hyperi-ci check

validate:
	$(PY) scripts/validate_schemas.py
