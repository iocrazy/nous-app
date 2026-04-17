#!/usr/bin/env bash
# scripts/sync-worktree.sh
#
# Sync the current worktree's branch with origin/master via rebase.
#
# Why rebase (not merge): keeps history linear and surfaces conflicts early on
# the feature branch instead of accumulating them into a giant "merge master"
# commit at PR-promote time.
#
# Why not "git pull": git pull defaults to merge in many setups; explicit rebase
# is the contract.
#
# This script enables `git rerere` (replay-recorded-resolution) so that once you
# resolve a conflict, the same conflict on a future rebase auto-resolves.
#
# Usage:
#   bash scripts/sync-worktree.sh                # sync to origin/master
#   bash scripts/sync-worktree.sh origin/dev     # sync to a different upstream
#
# Exit codes:
#   0  — clean rebase, branch up to date
#   2  — uncommitted changes, refused to rebase
#   3  — rebase produced conflicts (still in rebase state, fix manually)
#   4  — current branch is master/main (refuses to "sync" the integration branch)

set -euo pipefail

UPSTREAM="${1:-origin/master}"
CURRENT_BRANCH=$(git branch --show-current)

# Refuse to "sync" master onto itself
case "$CURRENT_BRANCH" in
  master|main)
    echo "✖  Current branch is '$CURRENT_BRANCH'. Don't sync the integration branch onto itself."
    echo "    Run this from a feature/* branch instead."
    exit 4
    ;;
esac

# Refuse if there are uncommitted changes (rebase would lose them or fail messily)
if ! git diff-index --quiet HEAD --; then
  echo "✖  You have uncommitted changes. Commit or stash first."
  echo ""
  git status --short
  exit 2
fi

# One-time: enable rerere globally for this repo
if [ "$(git config --get rerere.enabled || echo false)" != "true" ]; then
  echo "→  enabling git rerere (auto-replay conflict resolutions)"
  git config rerere.enabled true
  git config rerere.autoupdate true
fi

echo "→  fetching $UPSTREAM"
REMOTE_NAME="${UPSTREAM%%/*}"
git fetch "$REMOTE_NAME"

# Show how far behind/ahead we are
BEHIND=$(git rev-list --count "HEAD..$UPSTREAM")
AHEAD=$(git rev-list --count "$UPSTREAM..HEAD")
echo "→  branch '$CURRENT_BRANCH' is $AHEAD ahead, $BEHIND behind $UPSTREAM"

if [ "$BEHIND" -eq 0 ]; then
  echo "✓  already up to date with $UPSTREAM"
  exit 0
fi

echo "→  rebasing $CURRENT_BRANCH onto $UPSTREAM"
if ! git rebase "$UPSTREAM"; then
  echo ""
  echo "✖  rebase has conflicts. Resolve them, then run:"
  echo "    git add <files>"
  echo "    git rebase --continue"
  echo "  Or to abort:"
  echo "    git rebase --abort"
  exit 3
fi

NEW_BEHIND=$(git rev-list --count "HEAD..$UPSTREAM")
echo "✓  rebased clean — $CURRENT_BRANCH is now 0 behind $UPSTREAM"

# Reminder if the branch is published — force-push needed
if git rev-parse --verify "@{upstream}" >/dev/null 2>&1; then
  echo ""
  echo "ℹ  branch tracks a remote. Push with --force-with-lease:"
  echo "    git push --force-with-lease"
fi
