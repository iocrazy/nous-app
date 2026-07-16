# Runbook — refreshing `supabase/schema_baseline.sql`

The schema-drift gate (`.github/workflows/schema-drift.yml`) builds a throwaway
Postgres from three committed inputs and diffs it against the ORM models:

```
ci_bootstrap.sql  →  schema_baseline.sql  →  migrations numbered > watermark
```

`schema_baseline.sql` is a `pg_dump --schema-only` snapshot of prod's **actual
applied** `public` schema. Its header carries a **watermark** — the migration
number the snapshot already includes. CI applies only migrations *above* it.

This runbook covers regenerating that snapshot.

---

## Why a baseline (do not "just replay the migrations")

Replaying `supabase/migrations/*` in file-number order does not rebuild prod and
cannot. Measured 2026-07-15 by replaying all 371 forward migrations onto a
scratch database:

| setup | applied |
|---|---|
| bare Postgres | 86 / 371 |
| + supabase-shaped bootstrap | 342 / 371 |
| + storage/dbos stubs | ~346 / 371 |

The residue is not a bootstrap problem — **the file numbering is not the order
these migrations reached prod**:

- `069_project_folders` declares `project_id uuid REFERENCES projects(id)`, but
  by file order `051` has already made `projects.id` a bigint. The FK is
  impossible. Prod nonetheless has `project_folders.project_id = int8` and no
  later migration converts it → 069 reached prod *before* 051.
- `095` uses `CREATE POLICY IF NOT EXISTS`; `210` uses
  `ALTER PUBLICATION ... DROP TABLE IF EXISTS`. Neither is valid Postgres in any
  version — as committed they have never applied anywhere.
- `180_task_tracking_rename` is wrapped in `BEGIN/COMMIT` and fails on
  publication membership, so a replay ends with `unified_tasks` and **no**
  `task_tracking` — the inverse of prod.

A gate built on a replay would compare models against a schema that is
materially not prod: confidently wrong, which is worse than no gate.

---

## When to refresh

Refresh when the baseline drifts far enough from prod that CI's incremental
replay stops being a good proxy:

- **The incremental set gets long** (say >20 migrations above the watermark).
  Each one is re-applied on every CI run — slower, and more chances for a
  historical ordering quirk to resurface.
- **After a large or structural schema change** (table renames, type changes,
  a big epic landing) — especially one applied to prod by hand.
- **When a migration is known to have partially applied in prod.** The baseline
  records reality; the migration file records intent. When they disagree, only a
  re-dump tells the truth.
- **Not needed** for routine additive migrations. The gate handles those fine.

Refreshing is cheap and read-only. When in doubt, refresh.

---

## Procedure

### 1. Dump prod (read-only)

Run `pg_dump` **inside the prod container** so its version matches the server
(prod is PG 17.6); a newer client emits `\restrict` meta-commands that older
`psql` cannot parse.

```bash
ssh -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
  "sudo /usr/local/bin/docker exec mediahub-sb-prod-db \
     pg_dump -U supabase_admin -d postgres --schema-only --no-owner \
     --no-privileges -n public -T 'public._scratch_dbos' -T 'public._scratch_test'" \
  > /tmp/prod_dump_raw.sql
```

This only reads catalog metadata — it writes nothing. Never point this at prod
with anything but `pg_dump`.

### 2. Normalize (exactly two mechanical edits — nothing else)

```bash
grep -vE '^\\restrict|^\\unrestrict' /tmp/prod_dump_raw.sql \
  | sed 's/^CREATE SCHEMA public;$/CREATE SCHEMA IF NOT EXISTS public;/' \
  > /tmp/body.sql
```

1. `\restrict` / `\unrestrict` — client-side psql guards, not schema; they break
   older clients.
2. `CREATE SCHEMA public;` → `... IF NOT EXISTS ...` — every fresh database
   already has a public schema, so the raw line aborts the apply.

Do not hand-edit anything else. The file is generated; to change it, change prod
via a migration and re-dump.

### 3. Scan for secrets — MANDATORY, the repo is PUBLIC

A `--schema-only` dump should contain no data, but function bodies (especially
`SECURITY DEFINER` ones) can embed literals. Check before every commit:

```bash
# Secret-shaped tokens. Expect ONLY identifier names (api_key_status,
# api_key_logs, tokens_at_last_update, …) — never values.
grep -inE "(password|passwd|secret|api[_-]?key|token|bearer|private[_-]?key|\
BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]{10,}|sb_secret|sb_publishable|\
sk-[A-Za-z0-9]{16,}|postgres(ql)?://[^ ]*:[^ @]*@)" /tmp/body.sql

# Long string literals — eyeball every hit.
grep -oE "'[A-Za-z0-9+/=_.:-]{24,}'" /tmp/body.sql | sort -u

# Outbound/credential-bearing calls inside function bodies.
grep -inE "net\.http|http_post|http_get|dblink|vault\.|decrypted_secret|\
Authorization|service_role_key" /tmp/body.sql
```

As of the 2026-07-15 baseline the only long literals were a default download
path, a nil-UUID sentinel, two display-code alphabets, an error constant, and
sequence names; the third grep returned nothing. **If you find anything that
looks like a real credential, stop and report it — do not commit.**

### 4. Reassemble with an updated header

Keep the existing header block from `supabase/schema_baseline.sql` and update:

- `BASELINE WATERMARK:` → the **highest migration number currently on master**
  (`ls supabase/migrations | grep -Ev '_rollback\.sql$' | sort | tail -1`)
- `Generated:` → today
- `Contents:` → the new table count (`grep -c '^CREATE TABLE' /tmp/body.sql`)

Then `cat header body > supabase/schema_baseline.sql`.

**What the watermark does and does not claim.** It claims: every migration
numbered ≤ N is already reflected in this snapshot, so CI must not re-apply
them. It does **not** claim each of those files applied cleanly — several never
did (e.g. `176_drop_project_tasks` failed in prod, so `project_tasks` is still
live and still in the baseline). The snapshot is prod's reality, not the
migrations' intent. That distinction is the whole reason this file exists.

### 5. Verify before committing

Never trust the dump untested. Apply the full chain to a scratch database and
run the gate. **Use a scratch database on the dev cluster — never prod, and
never dev's own `postgres` database.**

```bash
export PGHOST=192.168.50.9 PGPORT=55434 PGUSER=supabase_admin
export PGPASSWORD="$(ssh -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
  "sudo /usr/local/bin/docker exec mediahub-sb-dev-db env | grep '^POSTGRES_PASSWORD='" \
  | cut -d= -f2)"

psql -d postgres -c "DROP DATABASE IF EXISTS baseline_verify;" \
                 -c "CREATE DATABASE baseline_verify;"
# The NAS disk fsyncs every commit; without this the apply crawls.
psql -d postgres -c "ALTER DATABASE baseline_verify SET synchronous_commit=off;"

psql -d baseline_verify -v ON_ERROR_STOP=1 -f supabase/ci_bootstrap.sql
psql -d baseline_verify -v ON_ERROR_STOP=1 -f supabase/schema_baseline.sql

cd backend
INTEGRATION_DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/baseline_verify" \
  uv run pytest tests/db/test_schema_drift.py -v

# ALWAYS clean up, and drop the credentials from your shell.
psql -d postgres -c "DROP DATABASE baseline_verify;"
unset PGPASSWORD
```

Expect all gates green. If a *ratchet* test fails saying an allowlist entry is
stale, that is the ratchet working: the drift it exempted is gone, so delete
that entry from `backend/tests/db/test_schema_drift.py` (see below).

### 6. After committing

The incremental set is now empty, so the next CI run tests the baseline alone.
That is expected and still a real run — the drift test always executes.

---

## The ratchet allowlists

`backend/tests/db/test_schema_drift.py` carries three allowlists of **known**
drift, so the gate is green today without hiding anything new:

| list | what it exempts | cleared by |
|---|---|---|
| `_ALLOWED_MISSING_TABLES` | 8 storyboard models whose tables mig 348 renamed to `zzz_deprecated_*` | deleting the dead models |
| `_ALLOWED_MISSING_COLUMNS` | 12 columns (6 tables) that exist live but not in the models | `sqlacodegen` regen |
| `_ALLOWED_UNMAPPED_TABLES` | 23 live tables with no model | adding the models |

Rules:

- **They only shrink.** `test_allowlists_only_shrink` asserts each against a
  recorded ceiling. Lower a ceiling when you fix drift; raising one is a
  deliberate, reviewable edit.
- **They cannot go stale.** `test_allowlists_are_still_accurate` fails if an
  entry no longer describes real drift, forcing its removal.
- **Only one direction is exemptible.** `_ALLOWED_MISSING_COLUMNS` covers
  live-has/model-lacks only. The reverse — a model column with no live column —
  is what makes ORM queries raise 42703 at runtime, is never exemptible, and
  currently has zero instances. Keep it that way.

A refreshed baseline can make entries stale (a drift got fixed in prod). Delete
those entries and lower the matching ceiling in the same PR.
