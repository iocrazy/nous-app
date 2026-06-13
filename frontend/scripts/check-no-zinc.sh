#!/usr/bin/env bash
# island redesign: zinc utilities are forbidden after the ink migration.
set -e
pattern='(^|[^a-zA-Z0-9-])(bg|text|border|ring|divide|placeholder|from|via|to|shadow|outline)-zinc-'
n=$(grep -rnoE "$pattern" \
  components pages App.tsx contexts --include="*.tsx" --include="*.ts" | wc -l | tr -d ' ')
if [ "$n" != "0" ]; then
  echo "FOUND $n forbidden *-zinc-* usages (use *-ink-* / semantic tokens):"
  grep -rnE "$pattern" components pages App.tsx contexts --include="*.tsx" --include="*.ts" | head -20
  exit 1
fi
echo "no zinc utilities — OK"
