#!/usr/bin/env bash
# Set up mediahub-sb-prod (PG 17, new supabase template) at temporary ports
# for parallel running with the old sb-mediahub during migration.
#
# Run on NAS:
#   sudo bash setup-prod-pg17.sh
#
# Reads:
#   /volume1/docker/datahub/sb-mediahub/.env           (old prod, for shared secrets)
#   /volume1/docker/datahub/mediahub-sb-dev/           (template source)
#
# Writes:
#   /volume1/docker/datahub/mediahub-sb-prod/.env
#   /volume1/docker/datahub/mediahub-sb-prod/docker-compose.yml
#   /volume1/docker/datahub/mediahub-sb-prod/docker-compose.pg17.yml
#   /volume1/docker/datahub/mediahub-sb-prod/volumes/...
#   /volume1/docker/datahub/mediahub-sb-prod/utils/...
#
# Old prod stack at sb-mediahub/ is NOT touched.
#
# Migration phase ports (do not conflict with old prod 9080/55432/6543/3080):
#   KONG_HTTP_PORT      = 9082
#   KONG_HTTPS_PORT     = 8495
#   POSTGRES_PORT       = 55434  (dev=55433, old prod=55432, prod-v2 migration=55434)
#   POOLER_PROXY_PORT   = 6545
#   STUDIO_PORT         = 3082
#   ANALYTICS_PORT      = 4002
#
# Once data migration verified and old prod retired, change .env to standard
# ports (9080/55432/6543/3080) and recreate stack.

set -euo pipefail

OLD_PROD=/volume1/docker/datahub/sb-mediahub
NEW_PROD=/volume1/docker/datahub/mediahub-sb-prod
DEV=/volume1/docker/datahub/mediahub-sb-dev

if [[ ! -f "$OLD_PROD/.env" ]]; then
  echo "ERROR: $OLD_PROD/.env not found — old prod stack required as secret source." >&2
  exit 1
fi

if [[ ! -f "$DEV/docker-compose.yml" ]]; then
  echo "ERROR: $DEV/docker-compose.yml not found — dev must be set up first as template." >&2
  exit 1
fi

if [[ -f "$NEW_PROD/docker-compose.yml" ]]; then
  echo "ERROR: $NEW_PROD/docker-compose.yml already exists — already set up." >&2
  exit 1
fi

mkdir -p "$NEW_PROD"

# ============================================================================
# 1. Copy template files from dev (already PG17 + customized)
# ============================================================================
echo ">> Copying compose template + utils + volumes from dev"
cp "$DEV/docker-compose.yml" "$NEW_PROD/docker-compose.yml"
cp "$DEV/docker-compose.pg17.yml" "$NEW_PROD/docker-compose.pg17.yml" 2>/dev/null || true
cp "$DEV/CHANGELOG.md" "$NEW_PROD/CHANGELOG.md" 2>/dev/null || true
cp "$DEV/env.example" "$NEW_PROD/env.example" 2>/dev/null || true

cp -R "$DEV/utils" "$NEW_PROD/utils"
cp -R "$DEV/volumes" "$NEW_PROD/volumes"
# But blow away dev's data!
rm -rf "$NEW_PROD/volumes/db/data"
mkdir -p "$NEW_PROD/volumes/db/data"
# Also clear pulled storage / snippets (those are user content, prod will re-seed)
rm -rf "$NEW_PROD/volumes/storage" "$NEW_PROD/volumes/snippets"
mkdir -p "$NEW_PROD/volumes/storage" "$NEW_PROD/volumes/snippets"

# ============================================================================
# 2. Rename project + containers from mediahub-sb-dev → mediahub-sb-prod
# ============================================================================
echo ">> Renaming compose for prod"
sed -i 's|name: mediahub-sb-dev|name: mediahub-sb-prod|g' "$NEW_PROD/docker-compose.yml"
sed -i 's|realtime-dev.mediahub-sb-dev-realtime|realtime-dev.mediahub-sb-prod-realtime|g' "$NEW_PROD/docker-compose.yml"
sed -i 's|container_name: mediahub-sb-dev-|container_name: mediahub-sb-prod-|g' "$NEW_PROD/docker-compose.yml"

sed -i 's|realtime-dev.mediahub-sb-dev-realtime|realtime-dev.mediahub-sb-prod-realtime|g' "$NEW_PROD/volumes/api/kong.yml"

# Studio external port: change 3081 (dev) -> 3082 (prod migration phase)
sed -i 's|"3081:3000"|"3082:3000"|g' "$NEW_PROD/docker-compose.yml"

# Workaround for docker compose v2.20.1 brace parser bug (NAS Container Manager
# ships ~2.20). Default JSON values in ${VAR:-{"keys":[]}} get parsed wrong,
# adding an extra } to the actual value. Remove default since add-new-auth-keys.sh
# always populates these env vars.
sed -i \
  -e 's|GOTRUE_JWT_KEYS: ${JWT_KEYS:-\[\]}|GOTRUE_JWT_KEYS: ${JWT_KEYS}|' \
  -e 's|API_JWT_JWKS: ${JWT_JWKS:-{"keys":\[\]}}|API_JWT_JWKS: ${JWT_JWKS}|' \
  -e 's|JWT_JWKS: ${JWT_JWKS:-{"keys":\[\]}}|JWT_JWKS: ${JWT_JWKS}|' \
  "$NEW_PROD/docker-compose.yml"

# ============================================================================
# 3. Generate .env — copy critical secrets from OLD prod, generate new for the rest
# ============================================================================
echo ">> Generating .env (preserving JWT_SECRET / ANON_KEY / SERVICE_ROLE_KEY / VAULT_ENC_KEY / PG_META_CRYPTO_KEY from old prod)"

# Source old prod's secrets
JWT_SECRET=$(grep '^JWT_SECRET=' "$OLD_PROD/.env" | cut -d= -f2-)
ANON_KEY=$(grep '^ANON_KEY=' "$OLD_PROD/.env" | cut -d= -f2-)
SERVICE_ROLE_KEY=$(grep '^SERVICE_ROLE_KEY=' "$OLD_PROD/.env" | cut -d= -f2-)
VAULT_ENC_KEY=$(grep '^VAULT_ENC_KEY=' "$OLD_PROD/.env" | cut -d= -f2-)
PG_META_CRYPTO_KEY=$(grep '^PG_META_CRYPTO_KEY=' "$OLD_PROD/.env" | cut -d= -f2-)
SECRET_KEY_BASE_OLD=$(grep '^SECRET_KEY_BASE=' "$OLD_PROD/.env" | cut -d= -f2-)

if [[ -z "$JWT_SECRET" || -z "$ANON_KEY" || -z "$SERVICE_ROLE_KEY" ]]; then
  echo "ERROR: failed to read JWT secrets from $OLD_PROD/.env" >&2
  exit 1
fi

# Generate fresh non-data secrets (these don't affect persisted data)
POSTGRES_PASSWORD="MediaHub_Prod_v2_$(openssl rand -base64 18 | tr -d '=+/' | head -c 24)"
DASHBOARD_PASSWORD="Prod_$(openssl rand -base64 12 | tr -d '=+/' | head -c 16)"
MCP_API_KEY="mcp_prod_$(openssl rand -base64 16 | tr -d '=+/' | head -c 24)"
LOGFLARE_PUB="$(openssl rand -base64 32 | tr -d '=+/' | head -c 40)"
LOGFLARE_PRIV="$(openssl rand -base64 32 | tr -d '=+/' | head -c 40)"
S3_KEY_ID="$(openssl rand -hex 16)"
S3_KEY_SECRET="$(openssl rand -hex 32)"

cat >"$NEW_PROD/.env" <<EOF
# mediahub-sb-prod env (generated $(date -u +%Y-%m-%dT%H:%M:%SZ))
#
# This is the new prod (PG 17 + new supabase template) running on TEMPORARY
# ports during data migration. Old prod sb-mediahub still on standard ports.
# After migration cutover, edit ports to standard values.

# Critical secrets COPIED from old sb-mediahub/.env (so existing JWTs / vault
# data continue to work after data restore).
JWT_SECRET=$JWT_SECRET
ANON_KEY=$ANON_KEY
SERVICE_ROLE_KEY=$SERVICE_ROLE_KEY
VAULT_ENC_KEY=$VAULT_ENC_KEY
PG_META_CRYPTO_KEY=$PG_META_CRYPTO_KEY

# New asymmetric keys (will be populated by add-new-auth-keys.sh)
SUPABASE_PUBLISHABLE_KEY=
SUPABASE_SECRET_KEY=
JWT_KEYS=
JWT_JWKS=
ANON_KEY_ASYMMETRIC=
SERVICE_ROLE_KEY_ASYMMETRIC=

# Fresh non-data secrets (rotated)
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DASHBOARD_USERNAME=heygo
DASHBOARD_PASSWORD=$DASHBOARD_PASSWORD
MCP_API_KEY=$MCP_API_KEY
SECRET_KEY_BASE=$SECRET_KEY_BASE_OLD

LOGFLARE_PUBLIC_ACCESS_TOKEN=$LOGFLARE_PUB
LOGFLARE_PRIVATE_ACCESS_TOKEN=$LOGFLARE_PRIV

S3_PROTOCOL_ACCESS_KEY_ID=$S3_KEY_ID
S3_PROTOCOL_ACCESS_KEY_SECRET=$S3_KEY_SECRET

# URLs (during migration phase, prod uses 9082; after cutover switch to 9080)
SUPABASE_PUBLIC_URL=http://192.168.50.9:9082
API_EXTERNAL_URL=http://192.168.50.9:9082
SITE_URL=http://192.168.50.9:9082

# DB (migration phase: 55434 to avoid conflict with dev=55433 and old prod=55432; after cutover: 55432 to match old prod backend config)
POSTGRES_HOST=db
POSTGRES_DB=postgres
POSTGRES_PORT=55434

# Pooler
POOLER_PROXY_PORT_TRANSACTION=6545
POOLER_DEFAULT_POOL_SIZE=20
POOLER_MAX_CLIENT_CONN=100
POOLER_TENANT_ID=heygo-prod
POOLER_DB_POOL_SIZE=5

# Kong (migration phase: 9082; after cutover: 9080)
KONG_HTTP_PORT=9082
KONG_HTTPS_PORT=8495

# Studio (migration phase: 3082; after cutover: 3080)
STUDIO_DEFAULT_ORGANIZATION=HEYGO
STUDIO_DEFAULT_PROJECT=MediaHub-PROD-V2

# Auth
JWT_EXPIRY=3600
DISABLE_SIGNUP=false
ADDITIONAL_REDIRECT_URLS=
MAILER_URLPATHS_CONFIRMATION="/auth/v1/verify"
MAILER_URLPATHS_INVITE="/auth/v1/verify"
MAILER_URLPATHS_RECOVERY="/auth/v1/verify"
MAILER_URLPATHS_EMAIL_CHANGE="/auth/v1/verify"
ENABLE_EMAIL_SIGNUP=true
ENABLE_EMAIL_AUTOCONFIRM=false
SMTP_ADMIN_EMAIL=8512939@qq.com
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=8512939@qq.com
SMTP_PASS=htqyxrzrabbybjch
SMTP_SENDER_NAME=HEYGO-Supabase
ENABLE_ANONYMOUS_USERS=true
ENABLE_PHONE_SIGNUP=true
ENABLE_PHONE_AUTOCONFIRM=true

# PostgREST
PGRST_DB_SCHEMAS=public,storage,graphql_public
PGRST_DB_MAX_ROWS=1000
PGRST_DB_EXTRA_SEARCH_PATH=public

# imgproxy (new template uses IMGPROXY_AUTO_WEBP, was IMGPROXY_ENABLE_WEBP_DETECTION)
IMGPROXY_AUTO_WEBP=true

# Functions
FUNCTIONS_VERIFY_JWT=false

# Storage S3 protocol (file backend by default; populate for S3 backend)
GLOBAL_S3_BUCKET=stub
REGION=stub
STORAGE_TENANT_ID=stub

# Analytics
DOCKER_SOCKET_LOCATION=/var/run/docker.sock
GOOGLE_PROJECT_ID=GOOGLE_PROJECT_ID
GOOGLE_PROJECT_NUMBER=GOOGLE_PROJECT_NUMBER

# OpenAI (optional)
OPENAI_API_KEY=

# Proxy (optional, only used with docker-compose.caddy.yml or .nginx.yml)
PROXY_DOMAIN=
CERTBOT_EMAIL=
EOF

chmod 600 "$NEW_PROD/.env"

# ============================================================================
# 4. Generate new asymmetric keys
# ============================================================================
echo ">> Generating asymmetric keys (sb_publishable / sb_secret / JWT_KEYS / JWT_JWKS)"
cd "$NEW_PROD"
sh utils/add-new-auth-keys.sh --update-env

# ============================================================================
# 5. Final summary
# ============================================================================
cat <<EOF

>> DONE.

Setup complete: $NEW_PROD

Container names:    mediahub-sb-prod-{db,kong,auth,rest,storage,...}  (13 containers)
Migration ports:    9082 (kong) / 55434 (pg) / 6545 (pooler) / 3082 (studio)
Standard ports:     9080 / 55432 / 6543 / 3080 (post-cutover)

Next steps:
  1. Verify config:
       cd $NEW_PROD
       cat .env | grep -E '^(POSTGRES_PORT|KONG_HTTP_PORT|STUDIO_PORT)='

  2. Start the new prod stack (PG 17 + new template):
       cd $NEW_PROD
       sudo docker compose up -d
       # Wait 5 min for PG 17 init, then:
       sudo docker compose ps
       # All 13 mediahub-sb-prod-* containers should be healthy

  3. Run data migration (separate script):
       sudo bash /volume1/docker/datahub/migrate-prod-data.sh

  4. Verify migrated data, then change ports to standard and switch backend.
     See docs/prod-migration-pg17.md for the full cutover checklist.

EOF
