# dfe-schemas -- schema validation.
#
# Requires dfe-engine importable. Set PY to an interpreter that has it,
# e.g. PY=../dfe-engine/.venv/bin/python, or run inside such a venv.

PY ?= python

.PHONY: validate

validate:
	$(PY) scripts/validate_schemas.py
