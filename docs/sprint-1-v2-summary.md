# Sprint 1 v2 — Boundary Layer + Agent Harness Primitives

> Branch: `feat/dbos-pr-d2`
> Period: 2026-05-01 → 2026-05-02
> Author: heygo (with Claude Code pair)
> Status: All planned phases complete + bonus harness primitives. Branch
> ready for merge to master.

## Summary in one paragraph

Built mediahub's first explicit **trust boundary layer** (`app/boundary/`)
covering SSRF defense, prompt-injection neutralization, and secret
log-redaction across 5 architectural layers. Then took the OpenClaw
research further and built **Agent Harness primitives**
(`app/agent_framework/`) covering small-model protection, multi-key
rotation, per-run abort, subprocess kill-tree, lifecycle event bus,
per-lane bounded concurrency, event-loop drift probe, and per-task-type
timeout policy. 39 commits, 1116/1116 backend tests passing, 16/17 e2e
smoke (1 fail = pre-existing Supabase pool exhaustion, unrelated).

## What got shipped

### Phase A — Boundary scaffold (4 commits)

- `docs/architecture/boundary-layer.md` — RFC + contract every caller follows
- `app/boundary/errors.py` — `BoundaryError` + `URLBlockedError` /
  `ExternalTextRejectedError` / `SecretMismatchError`
- `app/boundary/types.py` — `ValidatedURL(str)` wrapper for
  type-checker + runtime isinstance guard (mypy-not-in-CI design)
- `app/boundary/url_guard.py` — `validate_url` (sync) +
  `validate_url_async` (FastAPI async). SSRF defense:
  - scheme allowlist, RFC1918 + loopback + link-local + IPv6 ULA blocked
  - Non-canonical IPv4 (decimal `2130706433`, octal `0177.0.0.1`,
    hex `0x7f000001`) blocked
  - `.localhost` / `.local` / `.internal` / `.lan` / `.home` /
    `.intranet` / `.corp` / `.private` suffixes blocked
  - GCP/Azure metadata server hostnames blocked
  - DNS rebinding defense via per-resolver cache + per-IP validation
  - IPv6 zone strip, IPv4-mapped + IPv4-compatible edge cases
- `app/boundary/secret_compare.py` — `compare_secret` /
  `require_secret` via `hmac.compare_digest`
- Global `BoundaryError` exception handler in `app/core/exceptions.py`
  (returns 400 with safe message, never echoes raw rejected input)
- Settings: `SSRF_EXTRA_BLOCKED_NETWORKS` /
  `SSRF_DEV_ALLOWLIST` / `SSRF_DNS_TIMEOUT_SECONDS` /
  `SSRF_DNS_CACHE_TTL_SECONDS`

### Phase B — Boundary retro-fit (9 commits)

8 user-URL entry points retro-fitted to validate before any
service/workflow dispatch:

| Site | Action |
|---|---|
| B1 `media_fetch_router.fetch_video` | `validate_url_async` at API edge + `except BoundaryError: raise` |
| B2 `ytdlp_service.fetch_metadata / download_video / download_audio` | signatures `url: ValidatedURL` + runtime `assert isinstance` |
| B3 `downloader.download_file` | defensive `safe_async_client` (B9-C migration) |
| B4 `sb_ai_router /storyboard/analyze-video + /detect-scenes` | validate before workflow dispatch |
| B5 `visual_analysis._encode_image_from_url` | `safe_async_client` |
| B6 `media_batch_router /debug/raw-parse` | validate query URL |
| B7 `download_progress.download_file_with_progress` | `safe_async_client` |
| B8 `media_fetch_helpers.handle_media_fetch_dispatch` | `url: ValidatedURL` + assert |
| B10 secret_compare audit | grep verified — 0 retro-fit sites needed; `media_auth.py:86` already uses `hmac.compare_digest` |

### Phase B9 — Unified Outbound HTTP Boundary (8 commits + 1 fix)

5-layer architecture, mirrors OpenClaw `infra/net/*` + `security/*`:

| Layer | Module | Coverage |
|---|---|---|
| L1 validate_url | A4/A5 above | scheme/IP/host validation |
| L2 PinnedDNS | `app/boundary/pinned_dns.py` | resolve once, force connect to that IP — defeats DNS rebinding |
| L3 in-process | `app/boundary/safe_http.py` | `SafeAsyncClient`/`safe_async_client` httpx wrapper: per-request validate, per-redirect re-validate, cross-origin Authorization/Cookie/Proxy-Authorization strip |
| L3 cross-process | `app/boundary/ssrf_proxy.py` | Local HTTP/HTTPS forward proxy on `127.0.0.1:<random>`. Subprocess + browser clients route through it |
| L4 egress firewall | `docs/runbook/boundary-egress-firewall.md` | NAS DSM 7.x outbound deny rules — kernel-level backstop |
| L5 audit | `app/boundary/audit.py` + `supabase/migrations/185_boundary_audit.sql` + `app/api/admin/boundary_audit_router.py` | every block writes `boundary_audit` row + admin endpoint to query |

11 in-process httpx callers migrated to `safe_async_client`
(B9-C). 5 LLM/admin-config sites stay raw (per RFC trusted-config
carve-out). yt-dlp routes via `--proxy <SsrfProxy>` (B9-E).
DrissionPage routes via Chromium `set_proxy` + bypass override (B9-F).

### Phase C — External text neutralizer (3 commits + 1 gap fix)

- `app/boundary/external_text.py` — defang prompt-injection in untrusted
  text before LLM ingestion. Per-call random marker
  (`secrets.token_hex(8)`) prevents attacker from forging the close
  marker by writing it in their description. 22 known instruction-
  override literals bracketed (ChatML, Llama 2/3, Mistral, Gemma,
  Anthropic Human:/Assistant:, classic "ignore previous").
- Whitespace flooding collapsed (`\n{4,}` → `\n\n`).
- 2 retro-fits: `summarize_service` + `llm_analysis_service` wrap
  whisper transcript before LLM call. Prompt template explicitly
  references the `EXTERNAL_CONTENT_<marker_id>` block so the LLM
  knows the wrap is data not instructions.
- Verified NOT to wrap: `script_ai_service` (user-as-principal — premise/
  expansion are user creative input), `ai_library_chat_service` (chat
  user is principal), `storyboard_ai _annotate_keyframe` (server-built
  `f"Video frame at t=..."`), `visual_analysis` (image bytes only via
  safe_async_client).

### Phase D — Log redact (1 commit)

- `app/boundary/log_redact.py` — auto-mask secret-shaped tokens at log
  write time. Patterns calibrated to AVOID false positives on UUIDs,
  Snowflake BIGINTs (mediahub uses these as IDs), git commit SHAs:
  - Authorization: Bearer XXX (≥20 chars after prefix)
  - Bearer XXX (anywhere, ≥20 chars)
  - JWT 3-segment (each ≥16 chars — eliminates version.module.commit)
  - sk- API keys (≥20 chars)
  - KEY=value env style (KEY ends in KEY/TOKEN/SECRET/PASSWORD,
    value ≥12 chars)
  - token=xxx in query strings
- Loguru patcher walks `record["message"]` AND `record["extra"]` dict
  (E6 fix — covers `logger.bind(token=...).info(...)` leak path).
- Installed in `app/core/utils.py:Utils.setup_logging` BEFORE first
  `logger.add()` so all sinks (console, file, db_log_sink) see redacted
  form.

### Phase E — Cleanup + ship (1 commit)

- 3 stale Celery references in docstrings updated
  (agent_runner.py:382, hooks/__init__.py:13/115)
- 28 boundary commits + 1 D9 chore = ready to merge

### Phase F-1 — Agent Harness P0 primitives (4 commits)

OpenClaw-borrowed agent robustness primitives in `app/agent_framework/`:

| Module | OpenClaw ref | Solves |
|---|---|---|
| `context_window.py` (c1) | `agents/context-window-guard.ts` | small-model + heavy AGENT spec → no room for user input. WARN at 50% / REJECT at 80% of model's window. 7-family model table |
| `key_rotation.py` (c2) | `agents/api-key-rotation.ts` | single API key 429 lock-out. Round-robin N keys with per-key cooldown by HTTP status (429: 60s, 401/403: 1h) |
| `abort_controller.py` (c3) | `gateway/chat-abort.ts` | user "cancel" doesn't interrupt in-flight LLM call. `AbortController` + `race_until_abort(awaitable, ctl)` |
| `kill_tree.py` (c4) | `process/kill-tree.ts` | DBOS workflow cancel doesn't kill yt-dlp/whisper/ffmpeg subprocesses. `kill_process_tree(pid, grace_seconds=3)` SIGTERM→grace→SIGKILL via `os.killpg` |

c1 wired into `agent_runner.run_turn` as pre-flight check. c2-c4
ship as primitives; wiring into adapter/cancel-watcher is per-feature
follow-up.

### Phase G (D10) — Process isolation primitives (3 commits)

| Module | OpenClaw ref | Use |
|---|---|---|
| `lifecycle_bus.py` (D10-2) | `sessions/session-lifecycle-events.ts` | Pub/sub event bus. Listener exceptions isolated. Concurrent dispatch. Wildcard `"*"` subscription. Sync + async listeners |
| `lane_queue.py` (D10-3) | `process/command-queue.ts + lanes.ts` | Per-lane bounded concurrency. 4 named lanes (USER 5 / BACKGROUND 3 / SCHEDULED 1 / SUBAGENT 2). Per-lane task timeout, drain, snapshot |
| `event_loop_ready.py` (D10-6) | `gateway/event-loop-ready.ts` | Drift probe. `wait_for_loop_ready()` blocks until N consecutive readings below threshold. Avoids cold-start traffic hitting loaded loop |

Lifespan integration in `app/main.py`:
- `app.state.lifecycle_bus = LifecycleBus()`
- `app.state.lane_queue = LaneQueue()`
- `wait_for_loop_ready(threshold_ms=200, consecutive_passes=2,
  max_wait_seconds=10)` BEFORE `yield`

D10-4 (kill_tree) and D10-5 (AbortController) shipped via F-1.
D10-1 (gateway bound discovery) and D10-A (docker-compose physical
split) DEFERRED to next sprint — both depend on physical process
separation which is 1-2 weeks of architectural work + deployment
infra changes.

### D11 — Workflow timeout policy (1 commit)

- `app/agent_framework/workflow_timeout_policy.py` — per-workflow-type
  ceilings. parse=5min, ai_visual_analysis=60min, scheduled_sweep=2min,
  default 10min. 14 known types calibrated against actual mediahub
  workload patterns.
- `scheduled_recovery.recover_stale_orchestrator_locks_step` rewritten
  to use `is_stuck(task_type, elapsed)` instead of one-size 1h cutoff.
  Real workflows that take long (analyze) no longer false-positive
  reaped; quick workflows (parse) reaped sooner when actually stuck.

## Verification

- **1116/1116** backend unit tests passing (`uv run pytest --ignore=tests/integration`)
- **16/17** end-to-end smoke (`backend/scripts/smoke_boundary.py` —
  L1 11/11, L3 in-process 1/1, L3 proxy 4/4, L5 1 fail = pre-existing
  Supabase connection pool exhaustion, NOT a boundary regression)
- 78/78 `agent_framework` tests
- 154/154 `boundary` tests (incl. test_b1-b8 retro-fit integration tests)

## What's deferred

| Item | Reason |
|---|---|
| D10-1 gateway bound discovery | Depends on physical process split (D10-A) |
| **D10-A docker-compose split** | 1-2 weeks of architectural work + deployment infra changes — deserves its own sprint + design doc |
| F-1 c2/c3/c4 wiring (KeyRotator into adapter, AbortController watcher coroutine, kill_tree DBOS cancel hook) | Each touches different code paths — primitives shipped, integration per-feature follow-up |
| `secret_compare` retro-fits | Audit (B10) found 0 sites need it currently; module ships as preventive infrastructure |
| boundary_audit sweeper (90-day prune) | Listed as B9-G follow-up — not load-bearing |
| Phase 2 trusted-domain allowlist mode | Operations-model change, separate sprint |
| LLM eval before/after summarize prompt change | Needs LLM credits + human judgment, manual run |

## Key architectural decisions

1. **Trust boundary as a layer, not a sprinkling of guards.** Every
   external-to-trusted transition routes through one of the boundary
   modules. Service interiors `assert isinstance(url, ValidatedURL)`
   so type-checker + runtime guard catch raw-str bypasses (mypy not
   in CI yet — runtime assert is load-bearing).

2. **Defang, do not delete** for prompt injection. LLM still sees the
   text inside an `EXTERNAL_CONTENT_<random>` block; legitimate content
   that happens to look like an instruction is preserved. Random
   per-call marker prevents close-marker forgery.

3. **Best-effort audit.** boundary_audit DB write failure NEVER blocks
   the boundary block itself. Write failure logs at DEBUG and the
   reject still happens. Smoke confirmed graceful degradation when
   Supabase pool was exhausted.

4. **Layered enforcement, not single point.** Even if Layer 1 (validate)
   is bypassed by a bug, Layer 2 (pinned DNS) catches DNS rebinding,
   Layer 3 (SafeAsyncClient + SsrfProxy) catches redirects, Layer 4
   (egress firewall) catches at the kernel.

5. **Per-instance not global.** PinnedDNSResolver, KeyRotator,
   AbortController, LifecycleBus, LaneQueue are all per-process /
   per-run instances, not module-level globals. Avoids cross-test
   pollution and makes per-feature scoping easy.

6. **Primitives first, integration second.** Several modules ship as
   stand-alone primitives with their own tests, with wiring deferred
   to per-feature follow-up commits. Decoupling avoids one massive
   commit that touches many call sites.

## File structure additions

```
backend/app/
  boundary/                         (Sprint 1 v2 — boundary)
    __init__.py
    audit.py
    errors.py
    external_text.py
    log_redact.py
    pinned_dns.py
    safe_http.py
    secret_compare.py
    ssrf_proxy.py
    types.py
    url_guard.py
  agent_framework/                  (F-1 + Phase G + D11 — harness)
    __init__.py
    abort_controller.py
    context_window.py
    event_loop_ready.py
    key_rotation.py
    kill_tree.py
    lane_queue.py
    lifecycle_bus.py
    workflow_timeout_policy.py
backend/app/api/admin/boundary_audit_router.py
backend/scripts/smoke_boundary.py
backend/tests/boundary/         (10 test files, 154 tests)
backend/tests/agent_framework/  (8 test files, 78 tests)
docs/architecture/boundary-layer.md
docs/runbook/boundary-egress-firewall.md
supabase/migrations/185_boundary_audit.sql
```

## Commit log highlight

```
99b6497c D11        workflow_timeout_policy + per-type stuck reaper
918e5388 D10-3+6    LaneQueue + event-loop-ready
f7d1fafd D10-2      LifecycleBus
5ba82e5f F-1 c4     kill_process_tree
b8eb881c F-1 c3     AbortController
d75d1b55 F-1 c2     KeyRotator
82add1ae F-1 c1     context_window guard
17840278 Phase C    fix llm_analysis_service neutralize
c7b6e159 Phase E    Celery docstring sweep
a4a255c1 Phase D    log_redact + loguru patcher
7d8015e0 Phase C    summarize prompt update
7e2c9afd Phase C    external_text neutralizer
0c5e7203 review (a) smoke_boundary script
0b1c9d8a review (b) audit coverage gap fix
42014ce8 B9-H+I     firewall runbook + allowlist RFC
fce4857c B9-G       boundary_audit + admin endpoint
fd088db6 B9-F       DrissionPage via SsrfProxy
162cb74b B9-E       yt-dlp via SsrfProxy
06ecc9c1 B9-D       SsrfProxy
5ea55206 B9-C       migrate httpx callers
604543f4 B9-B       SafeAsyncClient
b9f8c063 B9-A       PinnedDNSResolver
4d4fcd1b B10        secret_compare audit
3d626f40 B3         downloader retro-fit
3869c7ac B2         ytdlp_service signatures
58dae69b B8         handle_media_fetch_dispatch
7fab4c32 B4+B5+B7   sb_ai + visual_analysis + download_progress
f43215a5 B6         debug/raw-parse
93ae8649 B1         media_fetch_router
3c845dd9 A6+A7      secret_compare + global handler
82f97dd5 A4+A5      url_guard
7561479b A2+A3      errors + ValidatedURL
dee56013 A1         RFC
```

39 commits = 28 boundary + 11 harness/D10/D11. Branch is
`feat/dbos-pr-d2`, all pushed to `origin`.
