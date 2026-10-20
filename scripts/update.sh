#!/usr/bin/env bash
# Updates Snappy from GitHub when it starts (called by run.sh, Linux install from a git clone).
#
# It never stands in the way of the launch: offline, a slow network (timeout), local edits not
# committed, Snappy already running from this folder, a conflict or a failed dependency install
# -> the current version starts as it is. What happened is written to ~/.cache/Snappy/update.log.
#
#   SNAPPY_NO_UPDATE=1          no update at all
#   SNAPPY_UPDATE_REF=origin/main   branch followed (default: origin/main)
#   SNAPPY_UPDATE_TIMEOUT=8     seconds allowed to reach GitHub
#
# Local commits (changes of your own, not published) are kept: they are replayed on top of the
# new version; if they conflict with it, nothing changes. Going back: git reset --hard ORIG_HEAD.
set -uo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." || exit 0

LOG="${XDG_CACHE_HOME:-$HOME/.cache}/Snappy/update.log"
mkdir -p "$(dirname "$LOG")"
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }
notify() { command -v notify-send >/dev/null && notify-send -a Snappy -i snappy "$@" 2>/dev/null; true; }

[[ -n "${SNAPPY_NO_UPDATE:-}" ]] && exit 0
[[ -d .git ]] || exit 0

# Snappy already running from this folder: its code must not change under it.
for pid in $(pgrep -u "$(id -u)" -f -- '-m snappy( |$)'); do
    if [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$PWD" ]]; then
        exit 0
    fi
done

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    log "local changes not committed: not updated"
    exit 0
fi

upstream="${SNAPPY_UPDATE_REF:-origin/main}"
remote="${upstream%%/*}"
branch="${upstream#*/}"
export GIT_TERMINAL_PROMPT=0
if ! timeout "${SNAPPY_UPDATE_TIMEOUT:-8}" git fetch --quiet "$remote" "$branch" 2>> "$LOG"; then
    log "could not reach $remote (offline?): not updated"
    exit 0
fi

before="$(git rev-parse HEAD)"
if git merge-base --is-ancestor "$upstream" HEAD; then
    exit 0                                          # already up to date
fi
if git merge-base --is-ancestor HEAD "$upstream"; then
    git merge --ff-only --quiet "$upstream" >> "$LOG" 2>&1 || { log "fast-forward failed"; exit 0; }
else
    # Local commits on top of an older version: replayed on top of the new one.
    if ! git rebase --quiet "$upstream" >> "$LOG" 2>&1; then
        git rebase --abort >> "$LOG" 2>&1
        log "your local commits conflict with $upstream: kept $(git rev-parse --short HEAD)"
        notify "Snappy" "Update available, but it conflicts with your local changes: not applied."
        exit 0
    fi
fi
after="$(git rev-parse HEAD)"

# New Python dependencies: installed, otherwise back to the previous version.
if ! git diff --quiet "$before" "$after" -- requirements.txt && [[ -x .venv/bin/python ]]; then
    if ! .venv/bin/python -m pip install --quiet -r requirements.txt >> "$LOG" 2>&1; then
        git reset --hard --quiet "$before"
        log "dependencies could not be installed: back to $(git rev-parse --short HEAD)"
        notify "Snappy" "Update not applied: its dependencies could not be installed."
        exit 0
    fi
fi

count="$(git rev-list --count "$before..$after" 2>/dev/null || echo "?")"
log "updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after") ($count commits)"
notify "Snappy updated" "$(git log -1 --format=%s "$upstream")"
