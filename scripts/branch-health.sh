#!/usr/bin/env bash
# scripts/branch-health.sh
#
# Audit local worktrees and warn about branches that have drifted too far from
# origin/master. Encourages the "rebase early, rebase often" rule that keeps
# merges from accumulating into multi-day cherry-pick + port marathons.
#
# Output: one row per worktree with ahead/behind counts and a verdict.
#
# Thresholds:
#   GREEN  — behind <  5 commits
#   YELLOW — behind 5-9 commits
#   RED    — behind >= 10 commits  (sync today, or PR will fight master)
#
# Usage:
#   bash scripts/branch-health.sh

set -euo pipefail

THRESHOLD_RED=10
THRESHOLD_YELLOW=5

git fetch origin master --quiet 2>/dev/null || true

printf "%-50s %-22s %8s %8s  %s\n" "WORKTREE" "BRANCH" "AHEAD" "BEHIND" "VERDICT"
printf "%-50s %-22s %8s %8s  %s\n" "$(printf '%.0s-' {1..50})" "$(printf '%.0s-' {1..22})" "--------" "--------" "-------"

git worktree list --porcelain | awk '
  /^worktree / { wt = $2 }
  /^branch / {
    sub("^refs/heads/", "", $2)
    print wt "\t" $2
  }
' | while IFS=$'\t' read -r wt branch; do
  if [ -z "${branch:-}" ]; then
    continue
  fi

  # Compute ahead/behind vs origin/master from this worktree's branch
  if ahead=$(git -C "$wt" rev-list --count "origin/master..$branch" 2>/dev/null) \
     && behind=$(git -C "$wt" rev-list --count "$branch..origin/master" 2>/dev/null); then
    :
  else
    ahead="?"; behind="?"
  fi

  case "$branch" in
    master|main)
      verdict="—  (integration branch)"
      ;;
    *)
      if [ "$behind" = "?" ]; then
        verdict="⚠  could not compute"
      elif [ "$behind" -ge "$THRESHOLD_RED" ]; then
        verdict="🔴  RED — sync today"
      elif [ "$behind" -ge "$THRESHOLD_YELLOW" ]; then
        verdict="🟡  YELLOW — sync this week"
      else
        verdict="🟢  GREEN"
      fi
      ;;
  esac

  printf "%-50s %-22s %8s %8s  %s\n" "$wt" "$branch" "$ahead" "$behind" "$verdict"
done

echo ""
echo "ℹ  to sync a worktree: cd <path> && bash scripts/sync-worktree.sh"
