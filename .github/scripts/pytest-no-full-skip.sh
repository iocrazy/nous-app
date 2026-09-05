#!/usr/bin/env bash
#
# Run pytest and REFUSE to report success when nothing actually ran.
#
# WHY THIS EXISTS
# ---------------
# `pytest` exits 0 when every test in a file skips. A file that skips itself
# because `INTEGRATION_DATABASE_URL` is unset therefore turns a bare
# `pytest <file>` step GREEN while asserting nothing — the exact "can't run →
# pass" path the schema-drift workflow's header forbids. The DSN is always set
# on that job, so a full skip there is a broken job, not a valid outcome.
#
# WHAT THE BAR IS
# ---------------
# "Something actually ran", NOT "nothing was skipped". A future
# `@pytest.mark.skipif` on one case (a PG-version-specific assertion, say) must
# not turn a step red while the other 17 pass. So the verdict is
# `passed_lines == 0`, and a partial skip with real passes stays green.
#
# HOW IT COUNTS
# -------------
# The summary line is the signal. awk (not `grep -q`) so a non-match yields a 0
# count rather than a non-zero exit this script would otherwise have to launder
# with `|| true` — which the schema-drift workflow forbids outright.
#
# pytest's own exit code is propagated untouched: a real failure (or exit 5,
# "collected 0 items") stays exactly as red as pytest made it, and the
# full-skip verdict only ever ADDS a red, never removes one.
#
# Usage (from any directory; args are passed straight to pytest):
#   bash .github/scripts/pytest-no-full-skip.sh tests/db/test_foo.py -v

set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "::error::pytest-no-full-skip.sh needs pytest arguments, e.g. tests/db/test_foo.py -v"
  exit 2
fi

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

uv run pytest "$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}

passed=$(awk '/[0-9]+ passed/ {n++} END {print n+0}' "$log")
skipped=$(awk '/[0-9]+ skipped/ {n++} END {print n+0}' "$log")
echo "summary check: exit=${rc} passed_lines=${passed} skipped_lines=${skipped}"

# pytest already said red — hand its verdict through unchanged.
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

if [ "$passed" -eq 0 ]; then
  echo "::error::pytest reported no passing tests for '$*' (passed=${passed} skipped=${skipped}). A skipped file is not a green file — check INTEGRATION_DATABASE_URL and the Postgres service."
  exit 1
fi
