#!/usr/bin/env bash
# Clone NAS Supabase prod stack into a dev/staging replica.
#
# Run on NAS:
#   bash clone-supabase-to-dev.sh
#
# Source: /volume1/docker/datahub/sb-mediahub
# Target: /volume1/docker/datahub/sb-mediahub-dev
# Action: copy compose + .env + volumes/api dir (NOT volumes/db or volumes/storage),
#         rewrite ports / container names / volume paths / public URLs,
#         regenerate JWT secret + anon/service_role keys,
#         leave dev DB schema empty for first boot (you sync schema later via pg_dump).
#
# Idempotent: re-running over an existing sb-mediahub-dev exits early.

set -euo pipefail

SRC=/volume1/docker/datahub/sb-mediahub
DST=/volume1/docker/datahub/sb-mediahub-dev

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: source $SRC does not exist" >&2
  exit 1
fi

if [[ -f "$DST/docker-compose.yml" ]]; then
  echo "ERROR: $DST/docker-compose.yml already exists — clone has already run." >&2
  echo "       Remove $DST or rename it if you want to re-clone." >&2
  exit 1
fi

echo ">> Copying compose + env + api volumes (NOT db/storage data)"
mkdir -p "$DST"
cp "$SRC/docker-compose.yml" "$DST/"
cp "$SRC/.env" "$DST/.env"
# Copy api config (kong.yml etc) but NOT user data (db/storage/snippets are user data)
mkdir -p "$DST/volumes"
cp -R "$SRC/volumes/api" "$DST/volumes/api"
[[ -d "$SRC/volumes/functions" ]] && cp -R "$SRC/volumes/functions" "$DST/volumes/functions" || true
[[ -d "$SRC/volumes/logs" ]] && mkdir -p "$DST/volumes/logs" || true
mkdir -p "$DST/volumes/db" "$DST/volumes/storage" "$DST/volumes/snippets"

echo ">> Rewriting ports in .env (+1 offset)"
sed -i \
  -e 's/^KONG_HTTP_PORT=.*/KONG_HTTP_PORT=9081/' \
  -e 's/^KONG_HTTPS_PORT=.*/KONG_HTTPS_PORT=8494/' \
  -e 's/^STUDIO_PORT=.*/STUDIO_PORT=3081/' \
  -e 's/^POSTGRES_PORT=.*/POSTGRES_PORT=55433/' \
  -e 's/^POOLER_PROXY_PORT_TRANSACTION=.*/POOLER_PROXY_PORT_TRANSACTION=6544/' \
  -e 's|^SITE_URL=.*|SITE_URL=http://192.168.50.9:9081|' \
  -e 's|^API_EXTERNAL_URL=.*|API_EXTERNAL_URL=http://192.168.50.9:9081|' \
  -e 's|^SUPABASE_PUBLIC_URL=.*|SUPABASE_PUBLIC_URL=http://192.168.50.9:9081|' \
  "$DST/.env"

echo ">> Generating fresh JWT secret + anon/service_role keys (DEV ONLY)"
NEW_JWT_SECRET=$(openssl rand -hex 32)
# Build anon + service_role JWTs for the new secret (5 year expiry, like supabase default).
IAT=$(date +%s)
EXP=$((IAT + 60*60*24*365*5))

python3 - <<PYEOF >>"$DST/.env.new"
import base64, hashlib, hmac, json, os
secret = "$NEW_JWT_SECRET".encode()
iat, exp = $IAT, $EXP
def b64(x):
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
def jwt(role):
    h = b64(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(',', ':')).encode())
    p = b64(json.dumps({"role":role,"iss":"supabase","iat":iat,"exp":exp}, separators=(',', ':')).encode())
    sig = b64(hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"
print(f"JWT_SECRET={secret.decode()}")
print(f"ANON_KEY={jwt('anon')}")
print(f"SERVICE_ROLE_KEY={jwt('service_role')}")
PYEOF

# Replace JWT_SECRET / ANON_KEY / SERVICE_ROLE_KEY in .env with the new ones
while IFS= read -r line; do
  key="${line%%=*}"
  sed -i "s|^${key}=.*|${line}|" "$DST/.env"
done <"$DST/.env.new"
rm -f "$DST/.env.new"

echo ">> Rewriting analytics hardcoded port 4000 -> 4001 in compose.yml"
sed -i 's|"4000:4000"|"4001:4000"|; s|- 4000:4000|- 4001:4000|' "$DST/docker-compose.yml"

echo ">> Rewriting container_name mediahub-* -> mediahub-sb-dev-*"
sed -i 's|container_name: mediahub-|container_name: mediahub-sb-dev-|g' "$DST/docker-compose.yml"
# realtime has special form: realtime-dev.mediahub-realtime
sed -i 's|realtime-dev\.mediahub-realtime|realtime-dev.mediahub-sb-dev-realtime|g' "$DST/docker-compose.yml"

echo ">> Rewriting volume host paths sb-mediahub -> sb-mediahub-dev"
sed -i 's|/volume1/docker/datahub/sb-mediahub/|/volume1/docker/datahub/sb-mediahub-dev/|g' "$DST/docker-compose.yml"

echo ">> Rewriting service hostnames in volumes/api/kong.yml (realtime cluster name etc.)"
if [[ -f "$DST/volumes/api/kong.yml" ]]; then
  sed -i 's|realtime-dev\.mediahub-realtime|realtime-dev.mediahub-sb-dev-realtime|g' "$DST/volumes/api/kong.yml"
fi

echo ">> Setting COMPOSE_PROJECT_NAME so the docker network is also isolated"
if ! grep -q '^COMPOSE_PROJECT_NAME=' "$DST/.env"; then
  echo 'COMPOSE_PROJECT_NAME=sb-mediahub-dev' >>"$DST/.env"
fi

cat <<EOF

>> DONE. Dev stack ready at $DST

Next steps (you run these manually):

  cd $DST
  sudo docker compose up -d
  # First start: takes ~2 min for all services healthy
  sudo docker compose ps          # all should be 'healthy' or 'running'

  # Verify dev stack is reachable + isolated:
  curl -s http://192.168.50.9:9081/auth/v1/health | jq .   # dev kong
  curl -s http://192.168.50.9:9080/auth/v1/health | jq .   # prod kong (untouched)

  # Sync schema (DDL only, NO data) from prod -> dev:
  sudo docker exec mediahub-sb-db pg_dump -U postgres -d postgres --schema-only --no-owner > /tmp/prod-schema.sql
  sudo docker exec -i mediahub-dev-db psql -U postgres -d postgres < /tmp/prod-schema.sql

  # Seed system rows (option β): ai_agents, skills (no user data)
  sudo docker exec mediahub-sb-db pg_dump -U postgres -d postgres \\
    --table public.ai_agents --table public.skills --table public.skill_files \\
    --table public.agent_skills --data-only --no-owner > /tmp/prod-seed.sql
  sudo docker exec -i mediahub-dev-db psql -U postgres -d postgres < /tmp/prod-seed.sql

The new keys are in $DST/.env. Save these to your dev backend/.env:

  SUPABASE_URL=http://192.168.50.9:9081
  SUPABASE_ANON_KEY=<from $DST/.env ANON_KEY>
  SUPABASE_SERVICE_ROLE_KEY=<from $DST/.env SERVICE_ROLE_KEY>

EOF
