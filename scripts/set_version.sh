#!/usr/bin/env bash
# Sets Snappy's version (snappy/__init__.py and pyproject.toml): asks for it, or takes it as argument.
#   scripts/set_version.sh          scripts/set_version.sh 0.3.0
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." || exit 1
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
exec "$PY" scripts/set_version.py "$@"
