# Team Chat — Agent Broadcast Implementation Plan (E2)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** An agent with the `auto_broadcast` capability proactively posts a task-completion status summary to a team channel it belongs to, with NO summoner present, NEVER touching any user-scope private data. Closes CHAT-AGENT-05, CHAT-PERM-11, CHAT-SEC-AGENT-05.

**Architecture (channel-driven, templated, LLM-free — confirmed safe design):** A DBOS scheduled scanner (~every 2 min) iterates the channels that have ≥1 `auto_broadcast` agent in `agent_channels`. Each such channel carries a `team_id`. For that team's members, it counts workflows that newly `completed` since a per-channel watermark (stored in `system_settings`). If any, it posts ONE templated summary message per channel ("✅ N tasks completed: …kind breakdown…") authored by the auto_broadcast agent (`sender_type='agent'`, `from_bot_agent_id` set → existing anti-loop ignores it). The message text is built ONLY from aggregate counts + the `task_kind` system enum — **no task titles, no `subtitle`, no `metadata`, no resource content** — so it is structurally impossible to leak `scope_type='user'` private data (SEC-AGENT-05). No LLM is invoked and no `resource_fetch` handler exists in this path. **No migration** (watermark → `system_settings`; channel mapping → `agent_channels`).

**Why templated/LLM-free for v1:** SEC-AGENT-05 is a hard no-leak constraint with no summoner to authorize reads. Not invoking the LLM at all (vs. running a tool-less agent turn) is the strongest possible guarantee and removes the entire prompt-injection / resource-leak attack surface. The `auto_broadcast` capability gate (PERM-11) + agent-as-sender are the meaningful controls. LLM-composed broadcast is a deliberate future enhancement, not v1.

**Tech Stack:** FastAPI/DBOS + `db_engine` + `system_settings`; admin AI-Library editor (verify the `auto_broadcast` toggle exists).

## Global Constraints
- **Branch:** `feature/chat-agent-broadcast` (off origin/master).
- **SEC-AGENT-05 (the binding security rule):** the broadcast message body MUST be derived ONLY from team-shareable, non-user-private data: aggregate counts + `task_kind` (a system enum). It MUST NOT include task `subtitle`/`metadata`/title or ANY resource content. No `resource_fetch`, no LLM, no service-role resource reads. A test must assert the summary contains only counts + kinds.
- **PERM-11:** only agents whose `agent_chat_caps(agent).auto_broadcast` is True (and `enabled` and `allows_team(team_id)`) may broadcast. Fail-closed (the parser defaults all to False).
- **task_tracking discipline (CLAUDE.md route C):** the scanner READS `task_tracking` only (status/completed_at/user_id/task_kind). It MUST NOT write or PATCH any task_tracking column. Broadcasts are written to `channel_messages` via the normal `send_message` business path.
- **Anti-loop:** broadcast messages set `from_bot_agent_id` → `dispatch_summons` already returns [] for bot-authored messages. A broadcast must never trigger an agent summon.
- **No first-scan backfill:** when a channel has no watermark yet, set the watermark to "now" and broadcast NOTHING (never dump all historical completions on first run).
- **Bounded + non-spammy:** at most ONE summary message per (channel) per scan, regardless of how many tasks completed. Cap the kind-breakdown rendering.
- **Idempotent / no double-post:** advance the per-channel watermark to the max `completed_at` consumed, only after a successful post. A crash before posting must not advance the watermark.
- **db_engine:** `from app.db import engine as db_engine`; `_bigint` coercion; named params; respect `system_settings.value` is jsonb (may return typed). Reuse `system_settings_repository` if it fits.
- **DBOS scheduled:** mirror `backend/app/workflows/liveness_scanner.py` / `scheduled_cleanup.py`; register in `backend/app/workflows/_scheduled_bundle.py`. Runs on the worker (gateway is enqueue-only) — fine.
- **i18n:** the broadcast TEXT is server-generated and posted as message content; keep it simple English (UI language rule). No new frontend strings expected.
- **Verification:** backend `uv run pytest` + black/isort/flake8 per task. Migration-free.

## File Structure
- `backend/app/repositories/chat_broadcast_repository.py` — read queries: broadcast-candidate channels, team member ids, completed-workflow counts-by-kind since a timestamp, watermark get/set.
- `backend/app/services/chat/agent_broadcast.py` — `scan_and_broadcast()` orchestration + the PERM-11 gate + templated summary builder.
- `backend/app/workflows/task_broadcast_scanner.py` — `@DBOS.scheduled` + `@DBOS.workflow` calling the service.
- `backend/app/workflows/_scheduled_bundle.py` — register the new scanner.
- `backend/tests/test_agent_broadcast.py` — gate, watermark, no-backfill, summary-safety, no-double-post.
- (verify) admin AI-Library agent editor — ensure the `auto_broadcast` checkbox is present.

---

## Task 1: Broadcast repository (read-only queries + watermark)

**Files:** Create `backend/app/repositories/chat_broadcast_repository.py`. Test: `backend/tests/test_agent_broadcast.py`.

**Interfaces (Produces):**
```python
class ChatBroadcastRepository:
    async def list_broadcast_candidate_channels(self) -> list[dict]  # [{channel_id:int, team_id:int, agent_ids:list[str]}] — channels (non-archived) that have ≥1 agent in agent_channels
    async def team_member_ids(self, team_id: int) -> list[str]       # user_ids of the team
    async def completed_workflow_counts_since(self, user_ids: list[str], since: datetime | None) -> dict  # {"total": int, "by_kind": {task_kind: count}, "max_completed_at": datetime|None} — status='completed' AND completed_at > since (or all if since None for the COUNT, but caller handles first-run)
    async def get_watermark(self, channel_id: int) -> datetime | None   # from system_settings key broadcast_watermark_channel_{id}
    async def set_watermark(self, channel_id: int, ts: datetime) -> None
```

- [ ] **Step 1: Write failing tests** in `backend/tests/test_agent_broadcast.py` (mock `db_engine` like `backend/tests/test_chat_*` do). Assert: `list_broadcast_candidate_channels` SQL joins `agent_channels`+`channels` (non-archived) and groups agents per channel; `completed_workflow_counts_since` SQL filters `status = 'completed'`, `task_kind = 'workflow'`, `completed_at > :since`, `user_id = ANY(:uids)` and returns total + by_kind + max_completed_at; the watermark getter/setter read/write `system_settings` under `broadcast_watermark_channel_{id}` (coerce value to/from ISO timestamp; remember system_settings.value is jsonb). `uv run pytest tests/test_agent_broadcast.py -v` → FAIL.
- [ ] **Step 2: Implement** the repo. Use `db_engine.fetch_all/fetch_one/execute`. For `team_member_ids`, query `team_members` (verify the table+columns via information_schema or an existing repo — likely `team_members(team_id, user_id)`). For the ANY(:uids) bind with asyncpg, mirror the existing `CAST(:uids AS uuid[])` / per-row patterns used elsewhere (check `increment_mentions` which loops to avoid array-bind; if array bind is awkward, build the query with an expanded IN clause from a bounded list, or loop — but counts need a single aggregate, so prefer `user_id = ANY(CAST(:uids AS uuid[]))` and test it; if asyncpg rejects, fall back to a safe expanded IN). Watermark via `system_settings` (reuse `system_settings_repository` if present; else direct upsert). Guard empty `user_ids` → return zero counts without querying.
- [ ] **Step 3:** `uv run pytest tests/test_agent_broadcast.py -v` → PASS (repo tests).
- [ ] **Step 4: Lint + commit.** black/isort/flake8. Commit — `feat(chat): broadcast repository (candidate channels, completion counts, watermark)`.

---

## Task 2: Broadcast service (PERM-11 gate + templated summary + orchestration)

**Files:** Create `backend/app/services/chat/agent_broadcast.py`. Modify nothing else. Test: extend `backend/tests/test_agent_broadcast.py`.

**Interfaces (Produces):**
```python
async def scan_and_broadcast() -> dict  # {"channels_scanned": int, "messages_posted": int}
def build_broadcast_summary(total: int, by_kind: dict[str, int]) -> str  # team-shareable text: counts + kinds only
```

- [ ] **Step 1: Write failing tests.** (a) PERM-11: a candidate channel whose only agent has `auto_broadcast=False` → no post (mock agent_repo + caps). (b) An agent with `auto_broadcast=True, enabled=True, allows_team(team)` → posts once. (c) **No-backfill:** channel with no watermark → set watermark≈now, post NOTHING. (d) **No-double-post:** second scan with no new completions → no post, watermark unchanged. (e) **Summary safety:** `build_broadcast_summary` output contains ONLY the count + kind names/counts — assert it does NOT contain any title/subtitle/metadata substrings (feed a by_kind and assert exact-ish format). (f) watermark advances to `max_completed_at` only after a successful post. Run → FAIL.
- [ ] **Step 2: Implement `build_broadcast_summary`** — pure function: e.g. `"✅ {total} task(s) completed" + " · " + ", ".join(f"{n} {kind}" for kind,n in by_kind)`. Only counts + kind enum. No other inputs. (UI English.)
- [ ] **Step 3: Implement `scan_and_broadcast`:**
  ```
  repo = ChatBroadcastRepository(); chat_repo = ChatRepository(); ar = get_agent_repository()
  for ch in await repo.list_broadcast_candidate_channels():
      # PERM-11: keep only auto_broadcast agents allowed for this team
      bc_agents = [a for aid in ch.agent_ids if (a := await ar.get_by_id(aid)) and agent_chat_caps(a).auto_broadcast and agent_chat_caps(a).enabled and agent_chat_caps(a).allows_team(ch.team_id)]
      if not bc_agents: continue
      wm = await repo.get_watermark(ch.channel_id)
      members = await repo.team_member_ids(ch.team_id)
      if not members: continue
      if wm is None:
          await repo.set_watermark(ch.channel_id, now_utc()); continue   # no backfill
      counts = await repo.completed_workflow_counts_since(members, wm)
      if counts["total"] <= 0: continue
      text = build_broadcast_summary(counts["total"], counts["by_kind"])
      # post once, authored by the FIRST eligible broadcast agent
      agent = bc_agents[0]
      await chat_repo.send_message(channel_id=ch.channel_id, sender_id=None, sender_type="agent", content_type="text", body={"text": text}, reply_to_id=None, from_bot_agent_id=agent["id"])
      await repo.set_watermark(ch.channel_id, counts["max_completed_at"])
      posted += 1
  ```
  Wrap each channel iteration in try/except that logs (loguru f-string, no %s) and continues — one bad channel must not abort the scan. `now_utc()` must come from the DB or `datetime.now(timezone.utc)` (this is app code, not a Workflow script — `datetime.now` is allowed here).
- [ ] **Step 4:** `uv run pytest tests/test_agent_broadcast.py -v` → PASS (all repo + service tests).
- [ ] **Step 5: Lint + commit.** Commit — `feat(chat): agent broadcast service (PERM-11 gate + templated team-safe summary)`.

---

## Task 3: DBOS scheduled scanner + registration

**Files:** Create `backend/app/workflows/task_broadcast_scanner.py`. Modify `backend/app/workflows/_scheduled_bundle.py`.

**Interfaces (Consumes):** `scan_and_broadcast` (Task 2).

- [ ] **Step 1:** Read `backend/app/workflows/liveness_scanner.py` + `scheduled_cleanup.py` + `_scheduled_bundle.py` for the exact `@DBOS.scheduled(cron)` + `@DBOS.workflow()` decoration, the `(scheduled_at, actual_at)` signature, async vs sync, and the bundle import style.
- [ ] **Step 2: Implement** `task_broadcast_scanner.py`: a `@DBOS.scheduled("0 */2 * * * *")` (every 2 min — match the 6-field cron style the other scanners use; copy their exact format) `@DBOS.workflow()` that calls `await scan_and_broadcast()` and returns its result dict. Must NEVER raise (wrap in try/except + log) so a failure doesn't poison the DBOS schedule. Keep it thin — all logic is in the service.
- [ ] **Step 3: Register** in `_scheduled_bundle.py` (add the `from app.workflows.task_broadcast_scanner import task_broadcast_workflow  # noqa: F401` import in the same style as the others).
- [ ] **Step 4:** `uv run pytest tests/test_agent_broadcast.py -v` still green; `cd backend && uv run python -c "import app.workflows._scheduled_bundle"` to confirm the bundle imports without error. Lint. Commit — `feat(chat): schedule agent-broadcast scanner (every 2 min)`.

---

## Task 4: Verify/add admin `auto_broadcast` toggle

**Files:** the AI-Library agent editor (locate: `frontend/` Settings → AI Library agent editor, or `admin/`). Possibly `frontend/` chat-permissions editor component.

- [ ] **Step 1:** Find where `chat_permissions` (`enabled` / `read_team_resources`) is edited in the agent editor UI (grep `read_team_resources` in `frontend/` and `admin/`). Confirm whether `auto_broadcast` has a control. The backend `ChatPermissionsIn/Out` already include `auto_broadcast`, so this is UI-only.
- [ ] **Step 2:** If `auto_broadcast` has NO control, add a checkbox/toggle next to the existing chat-permission toggles, bound to `chat_permissions.auto_broadcast`, with a short label ("Auto-broadcast task completions") + helper text noting it posts team-shareable status only. Match the existing toggle styling. i18n the label. If the control already exists, make NO change and note it in the report.
- [ ] **Step 3:** `npx tsc --noEmit` + `npm run build` (frontend) if changed. Commit (only if changed) — `feat(chat): auto_broadcast toggle in agent editor`.

---

## Self-Review
**Spec coverage:** CHAT-AGENT-05 (agent posts task-completion status to a team channel, no summoner) + CHAT-PERM-11 (auto_broadcast gate before posting) + CHAT-SEC-AGENT-05 (broadcast carries only team-shareable aggregate counts + system enums; structurally cannot read user-scope — no LLM, no resource_fetch).
**Deferrals:** LLM-composed broadcast text; per-task (vs aggregate) messages; broadcasting non-workflow task kinds; a dedicated `is_broadcast_channel` flag (v1 = any channel an auto_broadcast agent was added to); configurable cadence/quiet-hours. All future.
**Security review focus (make the final review adversarial on this):** prove no path in the broadcast carries user-private data — `build_broadcast_summary` takes only `(int, dict[str,int])`; the repo count query selects no content columns; no resource_fetch/LLM anywhere; PERM-11 fail-closed; no-backfill on first run; watermark prevents replay.
**Verification:** backend pytest (mocked) + bundle import; true E2E after deploy (enable auto_broadcast on an agent, add it to a team public channel, complete a workflow, see a count-only summary appear ~2 min later; verify a non-auto_broadcast agent stays silent).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review with an ADVERSARIAL security pass on SEC-AGENT-05 (no user-scope leak) + PERM-11 + watermark idempotency; then ship (backend → CI → ACR deploy → confirm scanner registered).
