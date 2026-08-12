# e2e-prod — real-stack production walkthrough

## Why this exists

A 2026-08 production incident shipped with `version.json`, `/api/v1/readyz`,
and CI all green — while the actual product was broken for real users. The
regular `e2e/` suite is a full network stub (see `e2e/helpers/stubs.ts`); it
never hits the real backend, so it can't catch "the deployed frontend talks
to the deployed backend and something in between is broken." This directory
closes that gap: **one read-only walkthrough, run against whatever is
currently live, using a dedicated test account and real data.**

Run it once after every frontend deploy:

```bash
npm run e2e:prod
```

This is the front-end mirror of CLAUDE.md's storage-deploy lesson "读正常 ≠
服务正常" (see 「部署验收纪律」) — here it's **"测试绿 ≠ 真栈正常"**: a green
CI run only proves the stubbed contract held, not that the real thing works.

## What it is NOT

- **Not part of the regular e2e suite.** `playwright.config.ts` (repo root)
  has `testDir: './e2e'` — it never sees this directory. `e2e-prod/` has its
  own `playwright.config.ts` (`testDir: '.'`, no `webServer` — there's
  nothing to build, it points at whatever is already deployed).
- **Not a CI gate.** Nobody wires this into `ci.yml` or any PR check. It's a
  manual (or externally-scheduled) post-deploy step, not a merge blocker —
  the target is by definition already live before the walkthrough can run
  against it.
- **Not a substitute for the regular e2e suite's coverage.** It exercises
  exactly one path end to end (login → project overview → storyboard →
  canvas → shot list → script editor). It's a smoke test, not a
  regression suite — its whole job is to fail loudly when the deployed
  frontend and deployed backend don't actually agree on anything, which the
  stubbed suite structurally cannot detect.

## Credentials — never in the repo

The walkthrough logs in as the project's dedicated Claude debug account
(`claude.debug@nous.test`). Credentials are read at runtime from one of:

1. `DEBUG_TEST_EMAIL` / `DEBUG_TEST_PASSWORD` environment variables, or
2. an out-of-tree `KEY=VALUE` file, path given by `CLAUDE_DEBUG_ENV_FILE`
   (default: `/media/heygo/program/datahub/nous/secrets/claude-debug.env`,
   mode `0600` — gpupc's existing convention for this account, see the
   `claude-debug-test-account` memory note).

Never paste the password into a conversation, commit, or this file. See
`helpers.ts::loadProdCreds` for the exact resolution order.

## Fixture data

The walkthrough targets a small, permanent QA project living under the
debug account's own **personal** workspace (not a team) — self-contained,
so the walkthrough never depends on another human's data or grants:

| | id |
|---|---|
| team (URL scope segment) | `331438215859255` (`claude.debug's Workspace`, the account's own personal team) |
| project ("QA Walkthrough") | `337650825568029` (`team_id: null` — a true personal project) |
| episode ("Episode 1", auto-provisioned with the project) | `337650825711390` |
| scene ("QA Walkthrough Room") | `337650952269612` |
| shot ("QA walkthrough probe shot") | `337650953731886` |

All five are overridable via `PROD_TEST_TEAM_ID` / `PROD_TEST_PROJECT_ID` /
`PROD_TEST_EPISODE_ID` / `PROD_TEST_SCENE_ID` / `PROD_TEST_SHOT_ID` if you
ever need to point this at different fixture data — see `helpers.ts`.

**Why not the "个人项目测试 1" project (`291022264100262`) an earlier memory
note mentions?** That project belongs to the *human* user's personal team,
not the debug account's. `GET /api/v1/projects/291022264100262` 403s for
the debug account (`"You do not have access to this project"`) — a
temporary `project_members` grant existed only during B4's point-ignition
and was removed afterward (see `project-b2-episode-workflow-and-followups`
memory note). Rather than depend on a human re-granting access before every
walkthrough run, this fixture lives entirely inside the debug account's own
scope.

**A non-obvious trap when creating personal-scope fixtures**: `POST
/api/v1/projects` with an explicit `team_id` (even the caller's *own*
personal team's snowflake id) creates a project the personal-scope list
query will never return — the backend's `team_id=personal` list filter
means `team_id IS NULL` (`projects_repository.py`), not "team_id equals my
personal team". The URL's `:teamId` segment is a separate concept (the
*workspace scope* shown in the sidebar) from the project row's own
`team_id` column. Create personal fixtures by omitting `team_id` from the
create body entirely (`project_type: "personal"`), not by passing the
personal team's id.

## What the walkthrough asserts (and why it's shaped this way)

All assertions are **visibility** assertions on real render output, never
bare DOM presence/count. This is deliberate: the 2026-08-12 canvas
duplicated-id incident's own regression test is the reason —
`.react-flow__node` nodes existed in the DOM but were permanently
`visibility:hidden`; a plain `toHaveCount(n)` passes on that broken build.
Every canvas assertion here checks `.react-flow__node:visible`.

1. **Login** — real UI click-through (landing → auth modal → real Supabase
   Auth call), not a seeded session. Exercises the actual login path a user
   goes through.
2. **Project workspace shell** — topbar + sidebar render.
3. **Overview accordion, expanded** — the current episode's row renders in
   its *expanded* state by default (workflow strip / node card), not just
   that the collapsed row exists.
4. **Storyboard module, view one** — the seeded scene's column and its shot
   card are visible.
5. **Canvas tab** — the real storyboard React Flow canvas; at least one
   node renders VISIBLE (not just present in the DOM).
6. **Shot List tab, view three** — the flat per-shot table, seeded shot row
   visible.
7. **Script module** — the inline-mounted editor shell loads.

## Extending it

Keep it read-only. If a future check needs to verify a write path, that's a
different, more careful walkthrough (or a synthetic-data cleanup story) —
don't bolt writes onto this one silently.
