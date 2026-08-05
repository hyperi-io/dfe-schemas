# dfe-schemas -- schema validation + reference DDL render.
#
# Requires dfe-engine importable. Set PY to an interpreter that has it,
# e.g. PY=../dfe-engine/.venv/bin/python, or run inside such a venv.
#
# `check` uses `git status --porcelain`, not `git diff`: a render can add or
# remove a file as well as change one, and git diff is blind to an untracked
# addition.

PY ?= python

.PHONY: validate render check

validate:
	$(PY) scripts/validate_schemas.py

render:
	$(PY) scripts/render_ddl.py

check: validate render
	test -z "$$(git status --porcelain argocd/ddl/)" \
	  || { echo "argocd/ddl/ is stale -- run 'make render' and commit"; git status --short argocd/ddl/; exit 1; }
