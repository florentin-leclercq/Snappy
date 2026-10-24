#!/usr/bin/env bash
# Runs Snappy from the sources (after ./install.sh: see README.md)
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
# Update to the latest GitHub release before starting (snappy/update.py), but never for a desktop
# shortcut (--hotkey): it must stay instant, and Snappy is already running then.
if [[ " $* " != *" --hotkey "* ]]; then
    "$PY" -m snappy.update || true
fi
exec "$PY" -m snappy "$@"
