#!/usr/bin/env bash
# Migrate data from old sb-mediahub (PG 15) to a target PG 17 stack.
#
# Run on NAS:
#   sudo bash migrate-prod-data.sh --check  --target=dev   # dry run against dev
#   sudo bash migrate-prod-data.sh --apply  --target=dev   # rehearsal: dump → dev
#   sudo bash migrate-prod-data.sh --verify --target=dev   # row count compare (dev)
#
#   sudo bash migrate-prod-data.sh --check  --target=prod  # dry run against new prod
#   sudo bash migrate-prod-data.sh --apply  --target=prod  # real migration → new prod
#   sudo bash migrate-prod-data.sh --verify --target=prod  # row count compare (new prod)
#
# --target is REQUIRED (no default — prevents accidentally hitting wrong stack).
#
# Prerequisites:
#   target=dev  → mediahub-sb-dev-db running (rehearsal target; dev should be near-empty)
#   target=prod → mediahub-sb-prod-db running (real migration target; new prod stack)
#   For --apply on prod: backend MUST be in maintenance mode (this script does NOT handle that).
#
# Strategy: pg_dump from old --schema-only --data-only separately, then restore.
# --no-owner --no-privileges so role grants on target stack apply correctly.

set -euo pipefail

OLD_DB=mediahub-db                  # old prod (PG 15) container name
DUMP_DIR=/tmp/mh-migration

MODE=""
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --check|--apply|--verify) MODE="$arg" ;;
    --target=dev)             TARGET=dev;  NEW_DB=mediahub-sb-dev-db ;;
    --target=prod)            TARGET=prod; NEW_DB=mediahub-sb-prod-db ;;
    *) echo "Unknown arg: $arg" >&2; MODE=""; break ;;
  esac
done

if [[ -z "$MODE" || -z "$TARGET" ]]; then
  cat <<USAGE
Usage:
  $0 --check  --target=dev|prod    # Show row counts on both stacks (no changes)
  $0 --apply  --target=dev|prod    # Run pg_dump from old + pg_restore to target (~30 min)
  $0 --verify --target=dev|prod    # Compare row counts post-migration

  --target=dev  → rehearsal: writes to mediahub-sb-dev-db
  --target=prod → real migration: writes to mediahub-sb-prod-db

Prerequisites:
  - Old prod sb-mediahub stack must be running
  - Target stack (dev or new prod) must be running
  - Backend in maintenance mode during --apply --target=prod
  - Disk: $DUMP_DIR needs ~2x your DB size in free space
USAGE
  exit 2
fi

echo ">> Mode: $MODE   Target: $TARGET ($NEW_DB)"
if [[ "$TARGET" == "prod" && "$MODE" == "--apply" ]]; then
  echo ">> WARNING: REAL prod migration. Backend must be in maintenance mode. Ctrl-C now if not."
  sleep 5
fi

ensure_running() {
  for c in "$OLD_DB" "$NEW_DB"; do
    if ! sudo docker inspect "$c" >/dev/null 2>&1; then
      echo "ERROR: container $c not found. Both stacks must be running." >&2
      exit 1
    fi
    health=$(sudo docker inspect "$c" --format '{{.State.Status}}')
    if [[ "$health" != "running" ]]; then
      echo "ERROR: $c is $health, expected running." >&2
      exit 1
    fi
  done
}

table_counts() {
  local container=$1
  sudo docker exec "$container" psql -U postgres -d postgres -At -c "
SELECT 'parsed_media',     COUNT(*) FROM parsed_media
UNION ALL SELECT 'resources',         COUNT(*) FROM resources
UNION ALL SELECT 'unified_tasks',     COUNT(*) FROM unified_tasks
UNION ALL SELECT 'projects',          COUNT(*) FROM projects
UNION ALL SELECT 'project_tasks',     COUNT(*) FROM project_tasks
UNION ALL SELECT 'teams',             COUNT(*) FROM teams
UNION ALL SELECT 'auth.users',        COUNT(*) FROM auth.users
UNION ALL SELECT 'storage.objects',   COUNT(*) FROM storage.objects
UNION ALL SELECT 'ai_agents',         COUNT(*) FROM ai_agents
UNION ALL SELECT 'skills',            COUNT(*) FROM skills
UNION ALL SELECT 'tags',              COUNT(*) FROM tags
ORDER BY 1;
" 2>&1
}

case "$MODE" in
  --check)
    echo ">> Pre-migration counts (sanity check)"
    ensure_running
    echo "OLD prod ($OLD_DB):"
    table_counts "$OLD_DB"
    echo ""
    echo "TARGET $TARGET ($NEW_DB) — should be near-empty (just init):"
    table_counts "$NEW_DB" || true
    ;;
  --apply)
    ensure_running
    mkdir -p "$DUMP_DIR"

    echo ">> [1/4] Dumping schema-only from old prod (NO data)"
    sudo docker exec "$OLD_DB" pg_dump -U postgres -d postgres \
      --schema-only --no-owner --no-privileges \
      --schema=public --schema=auth --schema=storage \
      > "$DUMP_DIR/schema.sql"
    echo "   schema dump: $(wc -l < "$DUMP_DIR/schema.sql") lines"

    echo ">> [2/4] Dumping data-only from old prod (skip log tables to save space)"
    # Skip noisy log tables (will repopulate naturally) — saves time + disk
    sudo docker exec "$OLD_DB" pg_dump -U postgres -d postgres \
      --data-only --no-owner --no-privileges \
      --schema=public --schema=auth --schema=storage \
      --exclude-table-data='public.application_logs' \
      --exclude-table-data='public.api_request_logs' \
      --exclude-table-data='public.frontend_error_logs' \
      --exclude-table-data='public.audit_logs' \
      --exclude-table-data='public.user_logs' \
      > "$DUMP_DIR/data.sql"
    echo "   data dump: $(wc -l < "$DUMP_DIR/data.sql") lines, $(du -h "$DUMP_DIR/data.sql" | cut -f1)"

    echo ">> [3/4] Restoring schema to new prod"
    # New prod already ran supabase init, so most schemas exist. Use --on-error stop=0
    # to continue past schema-already-exists errors. Real errors will surface in row counts.
    sudo docker exec -i "$NEW_DB" psql -U postgres -d postgres -v ON_ERROR_STOP=0 \
      < "$DUMP_DIR/schema.sql" > "$DUMP_DIR/schema-apply.log" 2>&1
    echo "   schema apply log: $DUMP_DIR/schema-apply.log"
    echo "   errors: $(grep -c ERROR "$DUMP_DIR/schema-apply.log" || true)"

    echo ">> [4/4] Restoring data to new prod (this is the slow step)"
    # Wrap data restore with session_replication_role=replica to disable FK checks
    # during insert. Required for tables with circular FK refs (agent_inbox <-> agent_tasks)
    # which otherwise fail to load any rows. Re-enabling at end revalidates constraints
    # for new writes; existing data won't be re-checked, but pg_dump's referential
    # integrity guarantees the data was already consistent on the source.
    {
      echo "SET session_replication_role = replica;"
      cat "$DUMP_DIR/data.sql"
      echo "SET session_replication_role = origin;"
    } | sudo docker exec -i "$NEW_DB" psql -U postgres -d postgres -v ON_ERROR_STOP=0 \
      > "$DUMP_DIR/data-apply.log" 2>&1
    echo "   data apply log: $DUMP_DIR/data-apply.log"
    echo "   errors: $(grep -c ERROR "$DUMP_DIR/data-apply.log" || true)"

    echo ""
    echo ">> Restore complete. Run with --verify to compare row counts."
    ;;
  --verify)
    ensure_running
    echo ">> Row count comparison ($OLD_DB vs $NEW_DB)"
    paste <(table_counts "$OLD_DB") <(table_counts "$NEW_DB") | column -t -s $'\t' || \
      paste <(table_counts "$OLD_DB") <(table_counts "$NEW_DB")
    echo ""
    echo "(Numbers may differ slightly for log tables that were excluded from migration.)"
    ;;
esac
