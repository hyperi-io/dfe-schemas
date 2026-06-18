# dfe-schemas -- schema validation + reference DDL render.
#
# Requires dfe-engine importable. Set PY to an interpreter that has it,
# e.g. PY=../dfe-engine/.venv/bin/python, or run inside such a venv.

PY ?= python

.PHONY: validate render check

validate:
	$(PY) scripts/validate_schemas.py

render:
	$(PY) scripts/render_ddl.py

check: validate render
	git diff --exit-code ddl/ || { echo "ddl/ is stale -- run 'make render' and commit"; exit 1; }
