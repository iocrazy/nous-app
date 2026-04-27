#!/usr/bin/env bash
# Sync prod supabase to dev/staging supabase.
#
# Run on NAS:
#   bash sync-prod-to-dev.sh α       # schema only
#   bash sync-prod-to-dev.sh β       # schema + system seeds
#   bash sync-prod-to-dev.sh γ-light # β + 100 sanitized parsed_media samples
#
# Requires: sb-mediahub (prod) and sb-mediahub-dev (dev) both running.
# Scripts.clone-supabase-to-dev.sh must have been run once first.

set -euo pipefail

MODE="${1:-α}"
PROD_DB=mediahub-sb-db
DEV_DB=mediahub-sb-dev-db
DEV_TEST_USER_ID='00000000-0000-0000-0000-000000000001'
DEV_TEST_EMAIL='test@dev.local'
DEV_TEST_PASSWORD='dev-password-only-not-prod'

dump_schema() {
  echo ">> α: dumping prod schema (DDL only)"
  sudo docker exec "$PROD_DB" \
    pg_dump -U postgres -d postgres \
    --schema-only --no-owner --no-privileges \
    --schema=public --schema=auth --schema=storage \
    > /tmp/mh-schema.sql

  echo ">> α: applying schema to dev (skip on conflict — schema may already exist)"
  sudo docker exec -i "$DEV_DB" \
    psql -U postgres -d postgres -v ON_ERROR_STOP=0 < /tmp/mh-schema.sql \
    > /tmp/mh-schema-apply.log 2>&1 || true
  echo "   log: /tmp/mh-schema-apply.log"
  rm -f /tmp/mh-schema.sql
}

dump_seeds() {
  echo ">> β: dumping system seed tables (data only, no user data)"
  local TABLES=(
    public.ai_agents
    public.skills
    public.skill_files
    public.agent_skills
    public.tags
  )
  local ARGS=""
  for t in "${TABLES[@]}"; do ARGS="$ARGS --table=$t"; done

  sudo docker exec "$PROD_DB" \
    pg_dump -U postgres -d postgres --data-only --no-owner $ARGS \
    > /tmp/mh-seeds.sql

  echo ">> β: loading seeds into dev"
  sudo docker exec -i "$DEV_DB" \
    psql -U postgres -d postgres < /tmp/mh-seeds.sql
  rm -f /tmp/mh-seeds.sql
}

create_dev_test_user() {
  echo ">> Creating dev test user $DEV_TEST_EMAIL (idempotent)"
  sudo docker exec -i "$DEV_DB" psql -U postgres -d postgres <<SQL
INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at, created_at, updated_at, role, aud, raw_user_meta_data)
VALUES ('$DEV_TEST_USER_ID', '$DEV_TEST_EMAIL',
        crypt('$DEV_TEST_PASSWORD', gen_salt('bf')),
        now(), now(), now(), 'authenticated', 'authenticated', '{"name":"dev test user"}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.user_profiles (id, email)
VALUES ('$DEV_TEST_USER_ID', '$DEV_TEST_EMAIL')
ON CONFLICT (id) DO NOTHING;
SQL
}

dump_user_samples() {
  echo ">> γ-light: sampling 100 most-recent parsed_media (reassigned to dev test user)"
  # 1. dump 100 parsed_media + their related resources/tags
  sudo docker exec "$PROD_DB" psql -U postgres -d postgres -c "
  CREATE TEMP TABLE _sample_media AS
    SELECT * FROM parsed_media ORDER BY created_at DESC LIMIT 100;
  \\copy _sample_media TO '/tmp/sample-media.csv' CSV HEADER;
  CREATE TEMP TABLE _sample_resources AS
    SELECT r.* FROM resources r
    WHERE r.media_id IN (SELECT id FROM _sample_media);
  \\copy _sample_resources TO '/tmp/sample-resources.csv' CSV HEADER;
  "
  sudo docker cp "$PROD_DB:/tmp/sample-media.csv" /tmp/sample-media.csv
  sudo docker cp "$PROD_DB:/tmp/sample-resources.csv" /tmp/sample-resources.csv
  sudo docker cp /tmp/sample-media.csv "$DEV_DB:/tmp/sample-media.csv"
  sudo docker cp /tmp/sample-resources.csv "$DEV_DB:/tmp/sample-resources.csv"

  # 2. load into dev with user_id rewritten to dev test user
  sudo docker exec -i "$DEV_DB" psql -U postgres -d postgres <<SQL
CREATE TEMP TABLE _stage_media (LIKE parsed_media INCLUDING DEFAULTS);
\\copy _stage_media FROM '/tmp/sample-media.csv' CSV HEADER;
UPDATE _stage_media SET user_id = '$DEV_TEST_USER_ID';
INSERT INTO parsed_media SELECT * FROM _stage_media ON CONFLICT (id) DO NOTHING;

CREATE TEMP TABLE _stage_resources (LIKE resources INCLUDING DEFAULTS);
\\copy _stage_resources FROM '/tmp/sample-resources.csv' CSV HEADER;
UPDATE _stage_resources SET user_id = '$DEV_TEST_USER_ID';
INSERT INTO resources SELECT * FROM _stage_resources ON CONFLICT (id) DO NOTHING;
SQL

  rm -f /tmp/sample-media.csv /tmp/sample-resources.csv
}

case "$MODE" in
  α|alpha) dump_schema ;;
  β|beta)  dump_schema; dump_seeds ;;
  γ-light|gamma-light) dump_schema; dump_seeds; create_dev_test_user; dump_user_samples ;;
  *) echo "ERROR: unknown mode $MODE (use: α, β, γ-light)"; exit 2 ;;
esac

echo ""
echo ">> DONE. Mode: $MODE"
echo ">> Sanity:"
sudo docker exec "$DEV_DB" psql -U postgres -d postgres -c "
SELECT 'tables' AS what, COUNT(*) FROM information_schema.tables WHERE table_schema='public'
UNION ALL SELECT 'parsed_media', (SELECT COUNT(*) FROM parsed_media)
UNION ALL SELECT 'resources',    (SELECT COUNT(*) FROM resources)
UNION ALL SELECT 'ai_agents',    (SELECT COUNT(*) FROM ai_agents)
UNION ALL SELECT 'skills',       (SELECT COUNT(*) FROM skills);
"
