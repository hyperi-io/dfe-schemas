# dfe-schemas -- schema validation + reference DDL render.
#
# Requires dfe-engine importable. Set PY to an interpreter that has it,
# e.g. PY=../dfe-engine/.venv/bin/python, or run inside such a venv.
#
# `check` fails until issue #9 is resolved: the renderer emits a
# version-nested tree, argocd/ddl/ is the flat tree kustomize mounts, and
# the committed files have been hand-edited since. CI runs `validate` only.

PY ?= python

.PHONY: validate render check

validate:
	$(PY) scripts/validate_schemas.py

render:
	$(PY) scripts/render_ddl.py

check: validate render
	test -z "$$(git status --porcelain argocd/ddl/)" \
	  || { echo "argocd/ddl/ does not match a fresh render -- see issue #9"; git status --short argocd/ddl/; exit 1; }
