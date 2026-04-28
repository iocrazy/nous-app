# Prod Migration to PG 17 + new Supabase template

eng review 2026-04-27 — namespace B (`mediahub-sb-prod`)

## What this is

Old prod `sb-mediahub` (PG 15.8 + supabase 2025-12 template) → new prod
`mediahub-sb-prod` (PG 17.6 + supabase 2026-04 template + asymmetric API keys).

Approach: **fresh new stack on temporary ports, pg_dump/restore data, cutover
ports**. Old stack stays runnable as fallback for 1 week.

## Naming convention (decided)

```
backend stack:    mediahub-app-{backend, redis, celery-worker, celery-beat, nginx}
admin:            mediahub-admin
supabase prod:    mediahub-sb-prod-{db, kong, auth, rest, storage, ...}
supabase dev:     mediahub-sb-dev-{db, kong, ...}
```

## Migration phases

### Phase 0 — Preparation (zero prod impact, ~30 min)

```bash
# On NAS (all scripts already dropped at /volume1/docker/datahub/)
ssh user@nas -p <port>

# 0.1 Verify dev stack works first (smoke test the new template)
sudo docker compose -f /volume1/docker/datahub/mediahub-sb-dev/docker-compose.yml ps
# All 13 mediahub-sb-dev-* containers should be healthy

# 0.2 Set up new prod stack (uses temporary ports 9082/55434/6545/3082)
sudo bash /volume1/docker/datahub/setup-prod-pg17.sh
# Reads old sb-mediahub/.env to preserve JWT_SECRET / ANON_KEY / SERVICE_ROLE_KEY
# / VAULT_ENC_KEY / PG_META_CRYPTO_KEY (so existing tokens + vault data survive)
# Generates fresh non-data secrets (POSTGRES_PASSWORD, DASHBOARD, etc.)
```

### Phase 1 — Start new prod (~10 min, no downtime)

```bash
# 1.1 Start new prod stack
cd /volume1/docker/datahub/mediahub-sb-prod
sudo docker compose up -d
sleep 300
sudo docker compose ps
# All 13 mediahub-sb-prod-* containers should be healthy
# PG 17 init runs on first start (~2-3 min)

# 1.2 Smoke test new prod (no users yet, just structure)
curl -s http://192.168.50.9:9082/auth/v1/health    # GoTrue
curl -s http://192.168.50.9:9082/rest/v1/          # PostgREST
```

### Phase 2 — Data migration (~30-45 min, full prod downtime)

**This phase requires backend in maintenance mode.** Users cannot log in,
upload, or read data while pg_dump | pg_restore is running.

```bash
# 2.1 Put backend in maintenance mode (return 503 from nginx, etc.)
# (Manual step — depends on your nginx/maintenance setup)

# 2.2 Dry-run row counts
sudo bash /volume1/docker/datahub/migrate-prod-data.sh --check --target=prod
# Confirm old prod has the expected data; new prod is mostly empty

# 2.3 Run actual migration
sudo bash /volume1/docker/datahub/migrate-prod-data.sh --apply --target=prod
# Steps: schema dump → restore + data dump → restore
# Excludes log tables (application_logs / api_request_logs / etc.) to save time/space
# Expected duration: 10-40 min depending on data volume

# 2.4 Verify
sudo bash /volume1/docker/datahub/migrate-prod-data.sh --verify --target=prod
# Compare row counts old vs new for critical tables
# Numbers MUST match (except excluded log tables)

# (For rehearsal against dev first, replace --target=prod with --target=dev.)
```

### Phase 3 — Cutover (~10 min downtime)

Once data verified on new prod:

```bash
# 3.1 Stop old prod (keeping its volumes for fallback)
cd /volume1/docker/datahub/sb-mediahub
sudo docker compose down  # NO -v: data preserved

# 3.2 Switch new prod to standard ports
cd /volume1/docker/datahub/mediahub-sb-prod
# Edit .env: change ports to standard
sudo sed -i \
  -e 's|^KONG_HTTP_PORT=9082|KONG_HTTP_PORT=9080|' \
  -e 's|^KONG_HTTPS_PORT=8495|KONG_HTTPS_PORT=8493|' \
  -e 's|^POSTGRES_PORT=5432|POSTGRES_PORT=55432|' \
  -e 's|^POOLER_PROXY_PORT_TRANSACTION=6545|POOLER_PROXY_PORT_TRANSACTION=6543|' \
  -e 's|9082|9080|g' \
  .env
# Studio external port too (compose.yml has hard-coded "3082:3000" — change)
sudo sed -i 's|"3082:3000"|"3080:3000"|' docker-compose.yml

# 3.3 Recreate new prod with standard ports
sudo docker compose down
sudo docker compose up -d
sleep 60
sudo docker compose ps

# 3.4 Smoke test (now on standard URLs)
curl -s https://mediahub.heygo.cn/auth/v1/health
curl -s https://sb-mediahub.heygo.cn:88/auth/v1/health  # external proxy

# 3.5 Backend redeploy (since SUPABASE_URL didn't change but SUPABASE_SERVICE_ROLE_KEY
# also didn't change in this migration, backend doesn't strictly need to redeploy.
# But if you generate new asymmetric keys for backend, redeploy now via GitHub Actions).

# 3.6 Lift maintenance mode
```

### Phase 4 — Verification (24h observation)

```bash
# 4.1 Watch error funnels
# Check application_logs every few hours for 24h
# Look for new schema/RLS errors that didn't exist on PG 15

# 4.2 Smoke test critical user flows
# Login, upload media, run AI task, view storyboard, etc.

# 4.3 Keep old prod containers stopped but volumes intact
ls /volume1/docker/datahub/sb-mediahub/volumes/db/data
# Don't delete for at least 1 week
```

### Phase 5 — Decommission old prod (1 week post-migration)

```bash
# 5.1 Verify no errors in 7 days of new prod operation
# 5.2 Archive old prod
sudo mv /volume1/docker/datahub/sb-mediahub /volume1/docker/datahub/sb-mediahub.archived-$(date +%Y%m%d)

# 5.3 Keep archive for 3 months as cold backup, then delete
```

## Rollback (any phase)

### Rollback during Phase 1-2 (haven't cut over yet)

```bash
# New prod failed to start or data migration failed
cd /volume1/docker/datahub/mediahub-sb-prod
sudo docker compose down -v          # nuke new prod
sudo rm -rf /volume1/docker/datahub/mediahub-sb-prod
# Old prod still on standard ports, never stopped
# Lift backend maintenance mode (if entered)
```

### Rollback during Phase 3 (cut over but issues found)

```bash
# Stop new prod, restart old
cd /volume1/docker/datahub/mediahub-sb-prod
sudo docker compose down

cd /volume1/docker/datahub/sb-mediahub
sudo docker compose up -d
# Old prod resumes on standard ports
# Backend redeploy is automatic since old SERVICE_ROLE_KEY still valid
```

### Rollback during Phase 4-5

Same as Phase 3 rollback. Don't run Phase 5 archival until you're sure.

## ⚠️ Critical PG 17 caveat (discovered 2026-04-27 in dev validation)

**PG 17 supabase image (17.6.1.084) FRESH INSTALL is buggy** — only
`supabase_admin` role exists, all other 11 supabase roles (`postgres`,
`anon`, `authenticated`, `service_role`, `authenticator`,
`supabase_auth_admin`, etc.) are missing. The bundled `roles.sql` ALTERs
fail silently because the roles don't exist.

This is a known supabase issue ([#18836](https://github.com/supabase/supabase/issues/18836)
and related). Supabase's recommended path is **PG 15 first, then upgrade
via `utils/upgrade-pg17.sh`**, not fresh PG 17.

### Recommended migration path (revised 2026-04-27)

1. **Phase 1**: Set up new prod stack on **PG 15.8.1.085** (matches old prod)
   — same supabase template, new naming (mediahub-sb-prod-*), new asymmetric
   keys, new env. Init works perfectly.
2. **Phase 2**: pg_dump from old prod (PG 15) → pg_restore to new prod (also
   PG 15). Schema + data migration is trivial since same major version.
3. **Phase 3**: Cutover ports + backend env.
4. **Phase 4 (later, separate maintenance window)**: Run
   `utils/upgrade-pg17.sh` on new prod to upgrade PG 15 → PG 17. This is
   supabase's blessed path.

**To use PG 15**: in setup-prod-pg17.sh, the line that copies dev compose
includes the PG 17 image override. Change post-copy:

```bash
sudo sed -i 's|supabase/postgres:17.6.1.084|supabase/postgres:15.8.1.085|' \
  /volume1/docker/datahub/mediahub-sb-prod/docker-compose.yml
```

(I'll update setup-prod-pg17.sh to do this automatically.)

### Old caveat (kept for reference only — do not follow if going PG 15 path)

**Required bootstrap** (run AFTER setup-prod-pg17.sh, BEFORE data migration):
dump all 12 supabase roles from old prod (PG 15) which has them, then load
into new prod, then re-set passwords for the new POSTGRES_PASSWORD.

```bash
PASSWORD=$(grep ^POSTGRES_PASSWORD= /volume1/docker/datahub/mediahub-sb-prod/.env | cut -d= -f2-)
PROD_PWD=$(grep ^POSTGRES_PASSWORD= /volume1/docker/datahub/sb-mediahub/.env | cut -d= -f2-)

# Dump role definitions from old prod (no passwords — those won't transfer)
sudo docker exec -e PGPASSWORD="$PROD_PWD" mediahub-db \
  pg_dumpall -U supabase_admin -h localhost -p 55432 --roles-only --no-role-passwords \
  > /tmp/prod-roles.sql

# Apply to new prod
sudo docker cp /tmp/prod-roles.sql mediahub-sb-prod-db:/tmp/prod-roles.sql
sudo docker exec -e PGPASSWORD="$PASSWORD" mediahub-sb-prod-db \
  psql -U supabase_admin -h localhost -p 55434 -d postgres -v ON_ERROR_STOP=0 \
  -f /tmp/prod-roles.sql

# Set dev passwords for service roles
sudo docker exec -e PGPASSWORD="$PASSWORD" mediahub-sb-prod-db \
  psql -U supabase_admin -h localhost -p 55434 -d postgres <<SQL
ALTER USER postgres WITH SUPERUSER LOGIN PASSWORD '$PASSWORD';
ALTER USER authenticator WITH PASSWORD '$PASSWORD';
ALTER USER pgbouncer WITH PASSWORD '$PASSWORD';
ALTER USER supabase_auth_admin WITH PASSWORD '$PASSWORD';
ALTER USER supabase_functions_admin WITH PASSWORD '$PASSWORD';
ALTER USER supabase_storage_admin WITH PASSWORD '$PASSWORD';
ALTER USER supabase_replication_admin WITH PASSWORD '$PASSWORD';
ALTER USER supabase_read_only_user WITH PASSWORD '$PASSWORD';
SQL
```

**Long-term fix:** file an issue with supabase/postgres about missing init
scripts in PG 17 image. For now, copying roles from PG 15 prod is reliable.

### Critical caveat: docker compose v2.20.1 brace parser bug

If your docker compose version is < 2.30 (NAS Container Manager bundles
~2.20), the official supabase template line:

```yaml
# Storage / Realtime / Auth services:
JWT_JWKS: ${JWT_JWKS:-{"keys":[]}}
```

has a brace-matching parser bug. The default value `{"keys":[]}` contains
`{` and `}` characters which the parser miscounts, appending an extra `}` to
the actual JWT_JWKS value. Storage gets `{"keys":[...]}}` (4 close braces) →
`JSON.parse` fails → "Unable to parse JWT_JWKS value to JSON".

**Workaround**: remove the default value (we always populate JWT_JWKS via
add-new-auth-keys.sh, so default is unreachable):

```yaml
# Auth service line 168 area
GOTRUE_JWT_KEYS: ${JWT_KEYS}        # was: ${JWT_KEYS:-[]}

# Realtime service line ~323
API_JWT_JWKS: ${JWT_JWKS}            # was: ${JWT_JWKS:-{"keys":[]}}

# Storage service line ~372
JWT_JWKS: ${JWT_JWKS}                # was: ${JWT_JWKS:-{"keys":[]}}
```

Apply via sed after running setup-prod-pg17.sh:

```bash
DEV_COMPOSE=/volume1/docker/datahub/mediahub-sb-prod/docker-compose.yml
sudo sed -i \
  -e 's|GOTRUE_JWT_KEYS: ${JWT_KEYS:-\[\]}|GOTRUE_JWT_KEYS: ${JWT_KEYS}|' \
  -e 's|API_JWT_JWKS: ${JWT_JWKS:-{"keys":\[\]}}|API_JWT_JWKS: ${JWT_JWKS}|' \
  -e 's|JWT_JWKS: ${JWT_JWKS:-{"keys":\[\]}}|JWT_JWKS: ${JWT_JWKS}|' \
  "$DEV_COMPOSE"
```

(Once docker compose v2.30+ is available on NAS, this workaround can be
reverted.)

### Additional caveat: JWT_KEYS / JWT_JWKS quoting

`utils/add-new-auth-keys.sh --update-env` writes `JWT_KEYS=[{...}]` and
`JWT_JWKS={"keys":[...]}` **unquoted** into .env. supabase storage + realtime
fail to parse these (`Error: Unable to parse JWT_JWKS value to JSON`) because
docker-compose env_file parser breaks on `{`, `}`, `:`, `,` without quotes.

After running `add-new-auth-keys.sh`, **always wrap** these two values in
single quotes:

```bash
python3 -c "
import pathlib, re
p = pathlib.Path('/volume1/docker/datahub/mediahub-sb-prod/.env')
t = p.read_text()
for k in ('JWT_KEYS', 'JWT_JWKS'):
    t = re.sub(rf'^({k})=([^\\\"\\'].*)\$', lambda m: f\"{m.group(1)}='{m.group(2).strip()}'\", t, flags=re.M)
p.write_text(t)
"
```

mediahub backend currently has hardcoded `postgres://postgres:...` connection
strings in several places (TODO: audit and migrate to `supabase_admin`).
Without intervention, backend services hit `FATAL: role "postgres" does not
exist` at startup against PG 17.

**Required compatibility migration** (run AFTER setup-prod-pg17.sh + before
data migration):

```bash
PASSWORD=$(grep ^POSTGRES_PASSWORD= /volume1/docker/datahub/mediahub-sb-prod/.env | cut -d= -f2-)
sudo docker exec -e PGPASSWORD="$PASSWORD" mediahub-sb-prod-db psql \
  -U supabase_admin -h localhost -p 55434 -d postgres <<SQL
-- Create postgres role for backwards compatibility with mediahub backend code
-- that hardcodes "postgres://postgres:..." connection strings.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
    CREATE ROLE postgres WITH SUPERUSER LOGIN BYPASSRLS CREATEDB CREATEROLE PASSWORD '\$PASSWORD';
  END IF;
END
\$\$;
GRANT pg_monitor TO postgres;
SQL
```

**Long-term fix (separate project):** audit mediahub backend code for
hardcoded `postgres` role references and migrate to `supabase_admin` per
[supabase guide](https://supabase.com/docs/guides/self-hosting/remove-superuser-access).
This is a 1-2 week task involving:
1. grep backend codebase for `postgres://postgres@`, `user=postgres`, etc.
2. Replace with `supabase_admin` or appropriate service-specific role
3. Re-test all service paths

For prod migration NOW, the compatibility CREATE ROLE workaround is sufficient.

## Why this approach is safe

1. **Old prod data never touched**: pg_dump is read-only on old. Old volumes
   stay intact through entire migration.
2. **Critical secrets preserved**: JWT_SECRET / ANON_KEY / SERVICE_ROLE_KEY /
   VAULT_ENC_KEY / PG_META_CRYPTO_KEY copied from old to new. Existing user
   sessions, vault encrypted data, sodium-encrypted cells all survive.
3. **Standard ports only switch in phase 3**: until then, both stacks live in
   parallel on different ports. Backend can be tested against new prod via
   :9082 before flipping.
4. **Excluded tables**: log tables (`application_logs`, `api_request_logs`,
   `audit_logs`, `frontend_error_logs`, `user_logs`) skipped — these are
   high-volume operational data that doesn't need preservation. Saves ~30 min
   migration time.
5. **Fallback**: old prod containers can be recreated from volumes any time.

## Known risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| pg_dump 兼容性 PG 15 → PG 17 | low | Both supabase images. Schema dump should be portable. |
| supabase init scripts on PG 17 collide with restored data | medium | Use `--on-error stop=0` during restore; review schema-apply.log for unexpected errors |
| Existing client JWTs reject after migration | low | We preserved JWT_SECRET so old JWTs validate |
| pgsodium / vault decryption fails | low | We preserved VAULT_ENC_KEY + PG_META_CRYPTO_KEY |
| Sequence values reset | medium | pg_dump --data-only includes sequence state. Verify post-migration: `SELECT last_value FROM <table>_id_seq` matches old |
| Backend can't connect | medium | SUPABASE_URL changes from old to new. After cutover ports match, backend reconnects automatically |
| Realtime subscriptions break | medium | Clients reconnect within 1-2 min via supabase-js auto-retry |

## Total time + downtime budget

- **Phase 0-1** (parallel running): ~40 min, zero user-facing downtime
- **Phase 2** (migration): ~30-45 min downtime
- **Phase 3** (cutover): ~10 min downtime
- **Phase 4-5** (observation, archival): no downtime

**Total user-facing downtime: ~40-55 minutes.** Recommended window: late evening
when active users are minimal.

## Post-migration: optional next steps

- Rotate POSTGRES_PASSWORD on new prod (already different from old prod by
  default in setup-prod-pg17.sh)
- Migrate clients to new asymmetric keys (`SUPABASE_PUBLISHABLE_KEY` /
  `SUPABASE_SECRET_KEY`) — this is a separate 1-2 week project requiring
  frontend Vercel env update + backend code change to use new keys
- Enable Storage S3 protocol (populate `S3_PROTOCOL_*` env, mount RustFS or MinIO)
- Set up cron `sync-prod-to-dev.sh` weekly (γ-light mode) for staging refresh
