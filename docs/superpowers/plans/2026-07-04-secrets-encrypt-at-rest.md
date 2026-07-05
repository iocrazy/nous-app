# Secrets Encrypt-at-Rest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every secret stored in Postgres (API keys, platform cookies, user BYOK provider keys, admin provider keys) is Fernet-encrypted at rest via the existing `app/core/secret_box.py`, with zero-downtime dual-read backfill, read-API masking, and — first of all — fixing the discovered prod misconfiguration that has the current encryption running on the public dev key.

**Architecture:** Reuse `secret_box.encrypt/decrypt` (Fernet + MultiFernet rotation, built-in dual-read: non-`gAAAAA` input passes through as legacy plaintext). Encryption lives at the REPOSITORY boundary (write-encrypt / read-decrypt) so all consumers stay unchanged; masking lives at the ROUTER boundary. Backfill is an idempotent app-side script (Fernet cannot run in SQL migrations) executed via container exec, safe to re-run.

**Tech Stack:** Python cryptography (Fernet/MultiFernet), SQLAlchemy 2.0 (read_scope/write_scope), FastAPI, pytest.

## Global Constraints

- Cipher module: `app/core/secret_box.py` ONLY — no new crypto code, no algorithm changes.
- Dual-read rule: every read path must tolerate legacy plaintext rows for the whole migration window (secret_box.decrypt already does this — never bypass it).
- Backfill scripts must be IDEMPOTENT (skip rows already starting with `gAAAAA`) and run via `docker exec mediahub-app-backend python -m scripts.<name>` — never as SQL migrations.
- No secret value may appear in logs, test fixtures beyond existing ones, or API list/get responses post-masking tasks.
- Strategy-C value parity everywhere else: response dict shapes unchanged except the explicit masking changes listed per task.
- Lint: black + isort + flake8 (NOT ruff). loguru `{}`/f-string, never `%s`. Tests: scope-mock unit + DSN-gated integration patterns already in repo.
-每 task 完成后跑 broad suite `uv run pytest tests/ -q -x --ignore=tests/integration | tail -3`，无 NEW failures 才 commit。

## ⚠️ Phase 0 findings baked into this plan (verified 2026-07-04)

- Host `.env` (`/volume1/docker/mediahub/docker/.env`) HAS `MEDIAHUB_TOKEN_ENCRYPTION_KEY` + `_OLD`, **but running containers `mediahub-app-backend` / `mediahub-app-worker` do NOT have the env var** → `secret_box` is on `DEV_TOKEN_ENCRYPTION_KEY` (public, committed). All prod `user_mcp_servers.bearer_token` ciphertexts are currently decryptable from the repo constant.
- Secret inventory: `api_keys.key_value` (plaintext, mig 039; `key_hash` exists for validate → encryption does NOT affect validate_key); `user_cookies.cookie_text`+`cookie_file` (plaintext); user BYOK keys inside `user_settings.settings_json.ai_settings.*` (plaintext jsonb, read via `ai_provider_helpers._load_user_ai_settings`); `system_settings` admin keys: `graph_extractor_api_key`, `graph_embedder_api_key`, `ai_module.embedding.api_key`, `ai_module.topic_scorer.api_key`, `telemetry.langfuse.secret_key`, `platform.ai_providers` (jsonb bundle, #990); `user_mcp_servers.bearer_token` (already wired, wrong key per above).
- Masking precedent: admin graph-memory GET returns `*_set` booleans (#710); api_keys router currently returns full `key_value` on list/get/update, `secret_key` once at create.

---

### Task 0 (OPERATIONAL, do first, no code): repair the prod key configuration

**Files:** none (NAS operation + verification only). Controller/human task — NOT for a code subagent.

- [ ] **Step 1: Determine what `_OLD` holds.** On NAS: `grep MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD /volume1/docker/mediahub/docker/.env` and compare (by sha256, never print) with the repo `DEV_TOKEN_ENCRYPTION_KEY` (`elPmFWmZ0El3Dtmj-vzj9k6tmKy8vZxaIKhikCWNavY=`). Expected outcome A: `_OLD` == DEV key (someone prepared rotation correctly). Outcome B: `_OLD` is something else → SET `_OLD` to the DEV key value now (rows are DEV-encrypted; without it decrypt breaks after restart).
- [ ] **Step 2: Recreate app containers so they pick up the env** (compose-config-change runbook — Watchtower never re-reads env_file): `cd /volume1/docker/mediahub && sudo docker compose -f docker/docker-compose.yml up -d --force-recreate mediahub-app-backend mediahub-app-worker` (确认 compose service 名; fallback: `docker stop -t0` + `docker start` 不会重读 env_file，必须用 compose up)。
- [ ] **Step 3: Verify:** `sudo docker exec mediahub-app-backend printenv MEDIAHUB_TOKEN_ENCRYPTION_KEY | head -c 6` shows SET; `curl https://mediahubserver.heygo.cn:88/health` 200 ×3; MCP-server list/edit smoke in UI (decrypt via _OLD fallback works).
- [ ] **Step 4: Rotate existing rows to the real key** — AFTER Task 1's rotate script exists. (Ordering note: Steps 1-3 now; Step 4 after Task 1 ships.)

### Task 1: shared backfill/rotate runner `scripts/rotate_secrets.py`

**Files:**
- Create: `backend/scripts/rotate_secrets.py`
- Test: `backend/tests/test_rotate_secrets.py`

**Interfaces:**
- Produces: `async def rotate_column(session, model, column_name: str, *, where=None) -> RotateStats` and CLI `python -m scripts.rotate_secrets --target user_mcp_servers|api_keys|cookies|all [--dry-run]`. `RotateStats = dataclass(scanned:int, encrypted:int, reencrypted:int, skipped_null:int)`.
- Logic per row value: `None` → skip; starts with `gAAAAA` → `decrypt()` (MultiFernet new→OLD→[dev via _OLD]) then `encrypt()` under primary key (= re-encrypt/rotate; if decrypt raises ValueError, log row PK + continue, count as failed); else (legacy plaintext) → `encrypt()`. UPDATE via write_scope in batches of 200.

- [ ] **Step 1: Write failing tests** — fake session pattern (copy the scope-mock fixture style from `tests/test_points_repository.py`): plaintext row → encrypted (starts gAAAAA); already-encrypted-with-primary → reencrypted count + output differs (new IV) but decrypts to same plaintext; None skipped; dry-run performs zero UPDATEs.
- [ ] **Step 2: Run tests, verify FAIL** (`uv run pytest tests/test_rotate_secrets.py -v` → import error).
- [ ] **Step 3: Implement** rotate_column + CLI with targets registry: `{"user_mcp_servers": (UserMcpServers, ["bearer_token"]), "api_keys": (ApiKeys, ["key_value"]), "cookies": (UserCookies, ["cookie_text","cookie_file"])}` (model 类名以 `app/models` 实际反射名为准，实现时先 grep).
- [ ] **Step 4: Tests PASS; broad suite no new failures; lint.**
- [ ] **Step 5: Commit** `feat(security): idempotent secret rotate/backfill runner`.

### Task 2: api_keys — encrypt at rest + mask list/get/update

**Files:**
- Modify: `backend/app/repositories/api_key_repository.py` (create/list/get/update read-write boundaries)
- Modify: `backend/app/api/api_key_router.py` (masking; create keeps one-time `secret_key` reveal)
- Test: `backend/tests/test_api_key_repository.py` (extend), router tests if存在
- Docs: update the SECRET-HANDLING BOUNDARY MAP docstring in the repo file

**Interfaces:**
- Repo writes: `create` encrypts `key_value` before insert (`from app.core.secret_box import encrypt as encrypt_secret`). Repo reads: rows keep ciphertext; NEW helper `def _mask_key(row: dict) -> dict` replaces `key_value` with `key_prefix + "…"` and adds `key_value_set: True`. `validate_key` untouched (hash-based, verify by test).
- Router: list/get/update return masked rows (breaking change to response field `key_value` — MUST check frontend consumers first: grep `key_value` in `frontend/`; if the UI renders it, change UI to show prefix+set state in the same task).

- [ ] Step 1: failing tests — create encrypts (bind value starts gAAAAA, decrypts back to original); list/get/update responses have no plaintext (`key_value` masked, `key_value_set` true); validate_key still matches by hash with encrypted storage.
- [ ] Step 2: FAIL run. Step 3: implement (repo boundary + `_mask_key` + router wiring + frontend grep/patch). Step 4: PASS + broad + `cd frontend && npm run build` if frontend touched. Step 5: commit `feat(security): api_keys encrypted at rest + masked reads`.
- [ ] Step 6: backfill on prod (after merge+deploy): `docker exec mediahub-app-backend python -m scripts.rotate_secrets --target api_keys` → verify `SELECT count(*) FROM api_keys WHERE key_value NOT LIKE 'gAAAAA%' AND key_value IS NOT NULL` = 0.

### Task 3: user_cookies — encrypt at rest

**Files:**
- Modify: `backend/app/repositories/cookies_repository.py` (upsert encrypts cookie_text/cookie_file; get_all_by_user / get_by_user_and_platform decrypt before return)
- Test: `backend/tests/test_cookies_repository.py`(或现有 cookies 单测文件, grep 定位) + integration retarget
- Docs: SECRET-HANDLING BOUNDARY MAP docstring update

**Interfaces:** consumers (downloaders/user_settings_router) unchanged — repo returns decrypted plaintext dicts exactly as today; only at-rest representation changes. Router list already masks (never returns cookie content — verified in cleanup review).

- [ ] Step 1: failing tests — upsert binds ciphertext (gAAAAA) for both columns, None passthrough; reads return original plaintext (round-trip through fake rows carrying ciphertext); legacy plaintext row read returns as-is (dual-read).
- [ ] Step 2-5: FAIL → implement → PASS + broad + lint → commit `feat(security): platform cookies encrypted at rest`.
- [ ] Step 6: prod backfill `--target cookies` + zero-plaintext verify query.

### Task 4: user BYOK provider keys (user_settings.settings_json.ai_settings)

**Files:**
- Modify: `backend/app/repositories/user_settings_repository.py` — in `patch_settings_json` encrypt any `ai_settings.*.api_key`-shaped string fields before merge; in reads (`get_by_user_id`/`_load`) decrypt the same fields.
- Create: `backend/app/core/secret_fields.py` — `SECRET_JSON_PATHS: list[tuple[str,...]]` + `def encrypt_paths(doc: dict, paths) -> dict` / `decrypt_paths` (pure, non-mutating, returns new dict; missing paths no-op) — reused by Task 5.
- Test: `backend/tests/test_secret_fields.py` + extend user_settings tests.

**Interfaces:** consumers (`ai_provider_helpers._load_user_ai_settings`, ai_settings_router) unchanged — they receive decrypted dicts. ⚠️ jsonb 合并语义: `_MERGE_SQL` 的 `||` 是 shallow merge on top-level keys — encrypting nested values does not change merge behavior (值替换整段 ai_settings 或其子键按现状), MUST NOT alter `_MERGE_SQL` itself (#485 P0). Implementation point: encrypt inside the patch dict BEFORE json.dumps binding; decrypt after row fetch.
- 实现前先 grep 真实 BYOK 字段名 (`ai_settings` 子结构里 api_key 出现的确切路径, e.g. `ai_settings.providers.<slug>.api_key` vs `ai_settings.<module>.api_key`) — SECRET_JSON_PATHS 用通配段 (`("ai_settings","*","api_key")` 语义: 任意中间键) 实现并测试。

- [ ] Step 1: failing tests — encrypt_paths/decrypt_paths round-trip + wildcard path + non-mutating; patch_settings_json binds ciphertext in the jsonb patch; read returns decrypted; legacy plaintext value read passes through.
- [ ] Step 2-5: FAIL → implement → PASS + broad(注意 merge 4/4 套件必须全绿) + lint → commit `feat(security): user BYOK keys encrypted inside settings_json`.
- [ ] Step 6: BYOK backfill — extend rotate_secrets with `--target byok` (iterate user_settings rows, transform settings_json via encrypt_paths, single UPDATE per row through the SAME `_MERGE_SQL`-equivalent whole-value update… ⚠️ NO: whole-value replace violates merge-not-replace ONLY for concurrent writers; backfill runs offline → acceptable, document it; use plain UPDATE settings_json = :new WHERE user_id = :uid inside write_scope). Prod run + verify query (`settings_json::text NOT LIKE '%gAAAAA%'` heuristics per known paths).

### Task 5: system_settings admin provider keys

**Files:**
- Modify: `backend/app/repositories/admin/system_settings_repository.py` — registry `SECRET_SETTING_KEYS = {"graph_extractor_api_key", "graph_embedder_api_key", "ai_module.embedding.api_key", "ai_module.topic_scorer.api_key", "telemetry.langfuse.secret_key"}` → upsert encrypts `value` when key ∈ registry (value is a jsonb str); reads decrypt. `platform.ai_providers` (jsonb bundle): use Task 4's `encrypt_paths` with its per-provider key paths (grep #990's shape first: likely `{slug: {api_key: …}}` → path `("*","api_key")`).
- Audit-Modify: DIRECT readers that bypass the admin repo — grep `system_settings` across `app/` (embedding_config.py, graph memory `from_settings`, langfuse config reader #992, topic_scorer, #990 platform provider reader) — each direct SELECT must decrypt via secret_box after fetch (import + one-line wrap), or be rerouted through the repo. Enumerate ALL in the task report; zero bypass left unwrapped.
- Test: extend `backend/tests/test_admin_system_settings_repository.py` + per-reader unit tests.

**Interfaces:** admin GET already masks graph_* as `*_set` (#710) — extend the same masking to the newly-encrypted keys in the admin settings GET (`settings_router`/graph-memory GET), and langfuse/platform admin reads if any return values.

- [ ] Step 1: failing tests — registry write encrypts / read decrypts; non-secret keys untouched byte-identical (jsonb typed values native — the known asyncpg-typed gotcha, bool/int values MUST NOT pass through encrypt); each direct reader decrypts legacy+cipher.
- [ ] Step 2-5: FAIL → implement → PASS + broad(governance/graph_memory router 套件必须全绿) + lint → commit `feat(security): admin provider keys encrypted in system_settings`.
- [ ] Step 6: backfill `--target system_settings` (registry keys + platform bundle) + verify.

### Task 6: rotation execution + enforcement + ledger close

- [ ] Step 1 (prod, after Tasks 1-5 deployed): run `rotate_secrets --target all`; verify zero-plaintext queries per table.
- [ ] Step 2: rotate off the dev key: confirm every ciphertext decrypts under primary (`--dry-run` reports 0 failures), then remove `MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD` from host .env + compose up recreate; re-smoke MCP/api-key/cookie flows.
- [ ] Step 3: mig 039's "always accessible" doc note superseded — add migration-less docs update: `backend/docs/runbook/` note + update SECRET-HANDLING BOUNDARY MAP docstrings (api_key/cookies) to the new reality; memory ledger 销账 (api_keys 明文 parked item)。
- [ ] Step 4: optional hardening — startup probe warns when `secret_box.is_configured()` is False in non-dev (extend existing startup checks in `app/main.py` lifespan), test + commit `feat(security): startup warning on unconfigured secret key`.

## Execution notes

- Ship order: Task 0(Steps1-3) → Task 1 → Tasks 2/3/4/5 각 single-PR sequential (money-tier review each: no plaintext in logs/fixtures, dual-read verified, consumer zero-touch traced) → Task 6.
- Rollback per task = revert PR; data stays readable either way thanks to dual-read decrypt.
- ⚠️ Do NOT run any backfill before Task 0 Steps 1-3 (else you'd encrypt under the public dev key).
