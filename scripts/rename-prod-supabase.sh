#!/usr/bin/env bash
# Rename prod supabase container_name from mediahub-* to mediahub-sb-*.
#
# Run on NAS:
#   bash rename-prod-supabase.sh --dry-run    # show what would change
#   bash rename-prod-supabase.sh --apply      # rewrite files + recreate stack (~3 min downtime)
#   bash rename-prod-supabase.sh --rollback   # revert files from .bak + recreate stack
#
# Targets:
#   /volume1/docker/datahub/sb-mediahub/docker-compose.yml
#     13 container_name lines (mediahub-* -> mediahub-sb-*)
#   /volume1/docker/datahub/sb-mediahub/volumes/api/kong.yml
#     2 realtime URL refs (mediahub-realtime -> mediahub-sb-realtime)
#
# Volumes (prod data) are NOT touched. `down` (no -v) preserves all data.
# Backend stack (mediahub-app-*) is renamed by GitHub Actions deploy after this PR
# is merged — that part is NOT in this script.

set -euo pipefail

ROOT=/volume1/docker/datahub/sb-mediahub
COMPOSE="$ROOT/docker-compose.yml"
KONG="$ROOT/volumes/api/kong.yml"

MODE="${1:-}"

case "$MODE" in
  --dry-run)
    echo ">> Dry run — showing what would change in $COMPOSE:"
    grep -nE "container_name: mediahub-" "$COMPOSE" || true
    echo ""
    echo ">> ...and in $KONG:"
    grep -nE "mediahub-realtime" "$KONG" || true
    echo ""
    echo ">> No changes made. Run with --apply to actually rename."
    exit 0
    ;;
  --apply)
    if [[ -f "$COMPOSE.bak.rename" ]]; then
      echo "ERROR: $COMPOSE.bak.rename already exists. Already applied? Run --rollback first." >&2
      exit 1
    fi
    cp "$COMPOSE" "$COMPOSE.bak.rename"
    cp "$KONG" "$KONG.bak.rename"

    echo ">> Rewriting container_name in compose.yml ..."
    sed -i 's|container_name: mediahub-|container_name: mediahub-sb-|g' "$COMPOSE"

    echo ">> Rewriting realtime cluster hostname ..."
    sed -i 's|realtime-dev\.mediahub-realtime|realtime-dev.mediahub-sb-realtime|g' "$COMPOSE"
    sed -i 's|realtime-dev\.mediahub-realtime|realtime-dev.mediahub-sb-realtime|g' "$KONG"

    echo ">> Sanity:"
    grep -cE "container_name: mediahub-sb-" "$COMPOSE" | xargs -I{} echo "   {} container_name lines now mediahub-sb-* (expected 12)"
    grep -cE "realtime-dev\.mediahub-sb-realtime" "$COMPOSE" | xargs -I{} echo "   {} realtime cluster ref in compose (expected 1)"
    grep -cE "realtime-dev\.mediahub-sb-realtime" "$KONG" | xargs -I{} echo "   {} realtime cluster ref in kong (expected 2)"

    echo ""
    echo ">> Recreating supabase stack (~3 min downtime starts NOW)..."
    cd "$ROOT"
    sudo docker compose down
    sudo docker compose up -d

    echo ""
    echo ">> Wait 2 min then run: sudo docker compose ps"
    echo ">> Backups left at: $COMPOSE.bak.rename, $KONG.bak.rename"
    echo ">> To roll back: bash $0 --rollback"
    ;;
  --rollback)
    if [[ ! -f "$COMPOSE.bak.rename" ]]; then
      echo "ERROR: no backup found at $COMPOSE.bak.rename — nothing to roll back." >&2
      exit 1
    fi
    cp "$COMPOSE.bak.rename" "$COMPOSE"
    cp "$KONG.bak.rename" "$KONG"
    rm "$COMPOSE.bak.rename" "$KONG.bak.rename"
    cd "$ROOT"
    sudo docker compose down
    sudo docker compose up -d
    echo ">> Rolled back. Old container names mediahub-* are back."
    ;;
  *)
    echo "Usage: $0 --dry-run | --apply | --rollback" >&2
    exit 2
    ;;
esac
