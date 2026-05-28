#!/bin/bash
# scripts/migrations/2026-05-28-storage-path-personal-team.sh
# One-shot: move files from teams/{user_uuid}/ to teams/{personal_team_snowflake}/
# on the NAS, and update the DB file_path values to match.
#
# Pre-conditions:
#   - Spec 1 PR-A + PR-C have shipped (every personal user has a personal team
#     snowflake, and resource_items.scope_id is the team snowflake string).
#   - Operator has snapshotted NAS storage (Synology snapshot recommended).
#
# Usage (on NAS, as the heygo user):
#   bash scripts/migrations/2026-05-28-storage-path-personal-team.sh DRY_RUN
#   bash scripts/migrations/2026-05-28-storage-path-personal-team.sh APPLY
#
# DRY_RUN prints the moves that would happen without performing them.
# APPLY performs the moves AND runs the companion SQL UPDATE.
#
# Idempotent: skips users whose source dir doesn't exist or whose
# destination dir already exists.

set -euo pipefail

MODE="${1:-DRY_RUN}"
STORAGE_ROOT="${STORAGE_ROOT:-/volume2/sources/MediaHub.library}"
DB_CONTAINER="${DB_CONTAINER:-mediahub-sb-prod-db}"
SQL_FILE="$(dirname "$0")/2026-05-28-storage-path-personal-team.sql"

if [ "$MODE" != "DRY_RUN" ] && [ "$MODE" != "APPLY" ]; then
    echo "ERROR: mode must be DRY_RUN or APPLY (got $MODE)" >&2
    exit 1
fi

echo "═══════════════════════════════════════════════════════════════════"
echo " Spec 1 PR-D — storage path migration"
echo " Mode:         $MODE"
echo " Storage root: $STORAGE_ROOT"
echo " DB container: $DB_CONTAINER"
echo " SQL file:     $SQL_FILE"
echo "═══════════════════════════════════════════════════════════════════"
echo

# Sanity: storage root + container exist
if [ ! -d "$STORAGE_ROOT/teams" ]; then
    echo "ERROR: $STORAGE_ROOT/teams does not exist" >&2
    exit 2
fi
if ! sudo /usr/local/bin/docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
    echo "ERROR: container $DB_CONTAINER not found" >&2
    exit 3
fi
if [ ! -f "$SQL_FILE" ]; then
    echo "ERROR: SQL file not found at $SQL_FILE" >&2
    exit 4
fi

# Pull (user_uuid → personal_team_snowflake) mapping from DB
mapping_file=$(mktemp)
trap "rm -f $mapping_file" EXIT

echo "▶ Querying personal-team mapping from DB..."
sudo /usr/local/bin/docker exec "$DB_CONTAINER" psql -U postgres -d postgres -tA -F'|' -c "
SELECT owner_id::text, id::text
  FROM public.teams
 WHERE kind = 'personal'
 ORDER BY id
" > "$mapping_file"

mapping_count=$(wc -l < "$mapping_file" | tr -d ' ')
echo "  $mapping_count personal teams found"
echo

# Iterate mappings, plan moves
move_count=0
skip_count_no_src=0
skip_count_dst_exists=0

while IFS='|' read -r user_uuid team_snowflake; do
    [ -z "$user_uuid" ] && continue

    src="$STORAGE_ROOT/teams/$user_uuid"
    dst="$STORAGE_ROOT/teams/$team_snowflake"

    if [ ! -d "$src" ]; then
        skip_count_no_src=$((skip_count_no_src + 1))
        continue
    fi
    if [ -d "$dst" ]; then
        echo "⚠ SKIP (dst exists): $user_uuid → $team_snowflake"
        skip_count_dst_exists=$((skip_count_dst_exists + 1))
        continue
    fi

    if [ "$MODE" = "APPLY" ]; then
        echo "▶ MV: $user_uuid → $team_snowflake"
        sudo mv "$src" "$dst"
    else
        echo "  (dry) would mv $user_uuid → $team_snowflake"
    fi
    move_count=$((move_count + 1))
done < "$mapping_file"

echo
echo "═══════════════════════════════════════════════════════════════════"
echo " File-system summary:"
echo "   moves attempted:        $move_count"
echo "   skipped (no src dir):   $skip_count_no_src"
echo "   skipped (dst exists):   $skip_count_dst_exists"
echo "═══════════════════════════════════════════════════════════════════"
echo

if [ "$MODE" = "APPLY" ]; then
    echo "▶ Applying companion SQL UPDATE..."
    sudo /usr/local/bin/docker exec -i "$DB_CONTAINER" psql -U postgres -d postgres < "$SQL_FILE"
    echo
    echo "▶ Verification: counting resources.file_path entries still embedding a UUID..."
    sudo /usr/local/bin/docker exec "$DB_CONTAINER" psql -U postgres -d postgres -c "
        SELECT
          (SELECT COUNT(*) FROM public.resources
            WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}') AS resources_uuid_remaining,
          (SELECT COUNT(*) FROM public.resource_versions
            WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}') AS versions_uuid_remaining
        ;
    "
    echo
    echo "✓ APPLY complete."
else
    echo "(dry-run only — no file moves or DB updates performed)"
    echo "Re-run with APPLY to execute."
fi
