#!/usr/bin/env bash
# Keeps data/state.json on a separate "bot-state" branch so the bot never commits to main.
# Usage: scripts/state.sh restore   (before the bot runs)
#        scripts/state.sh save      (after the bot runs)
set -euo pipefail

BRANCH=bot-state
WORKTREE="${RUNNER_TEMP:-/tmp}/bot-state"

has_branch() { git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; }

case "${1:-}" in
  restore)
    mkdir -p data
    if has_branch; then
      git fetch --depth=1 origin "$BRANCH:refs/remotes/origin/$BRANCH"
      git show "origin/$BRANCH:state.json" > data/state.json.tmp
      mv data/state.json.tmp data/state.json
      echo "state restored"
    else
      echo "no saved state yet, starting fresh"
    fi
    ;;
  save)
    [ -f data/state.json ] || { echo "nothing to save"; exit 0; }
    git config user.name "job-bot"
    git config user.email "job-bot@users.noreply.github.com"
    rm -rf "$WORKTREE"
    if has_branch; then
      git worktree add -B "$BRANCH" "$WORKTREE" "origin/$BRANCH"
    else
      git worktree add --orphan -b "$BRANCH" "$WORKTREE"
    fi
    cp data/state.json "$WORKTREE/state.json"
    cd "$WORKTREE"
    git add state.json
    if git diff --cached --quiet; then
      echo "state unchanged"
    else
      git commit -q -m "Обновление состояния бота"
      git push -q origin "$BRANCH"
      echo "state saved"
    fi
    ;;
  *)
    echo "usage: $0 restore|save" >&2; exit 2 ;;
esac
