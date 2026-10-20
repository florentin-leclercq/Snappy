#!/usr/bin/env bash
# Runs Snappy from the sources (after ./install.sh: see README.md)
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# Update from GitHub before starting (scripts/update.sh), but never for a desktop shortcut
# (--hotkey): it must stay instant, and Snappy is already running then.
if [[ " $* " != *" --hotkey "* && -x scripts/update.sh ]]; then
    scripts/update.sh || true
fi
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m snappy "$@"
fi
exec python3 -m snappy "$@"
