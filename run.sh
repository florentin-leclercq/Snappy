#!/usr/bin/env bash
# Runs Snappy from the sources (after ./install.sh: see README.md)
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m snappy "$@"
fi
exec python3 -m snappy "$@"
