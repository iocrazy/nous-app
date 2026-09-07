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
  canvas → shot list → script editor → project materials → Generated inbox
  → Assets codex).
  It's a smoke test, not a regression suite — its whole job is to fail loudly
  when the deployed frontend and deployed backend don't actually agree on
  anything, which the stubbed suite structurally cannot detect.

## Credentials — never in the repo

The walkthrough logs in as the project's dedicated Claude debug account
(`claude.debug@nous.test`). Credentials are read at runtime from one of:

1. `DEBUG_TEST_EMAIL` / `DEBUG_TEST_PASSWORD` environment variables, or
2. `CLAUDE_DEBUG_ENV_FILE` — an explicit path to an out-of-tree `KEY=VALUE`
   file. ⚠️ If set but unreadable this **throws**; it does not fall through
   to the defaults. An explicit override that gets silently ignored would run
   the suite against the wrong account and you'd never know.
3. Otherwise the default candidates, first readable one wins:
   - `~/.nous/claude-debug.env` — machine-agnostic, works on whichever box is
     the dev machine today. Create it with:
     ```bash
     mkdir -p ~/.nous && chmod 700 ~/.nous
     # write DEBUG_TEST_EMAIL / DEBUG_TEST_PASSWORD into it, then:
     chmod 600 ~/.nous/claude-debug.env
     ```
   - `/media/heygo/program/datahub/nous/secrets/claude-debug.env` — gpupc's
     historical convention (see the `claude-debug-test-account` memory note),
     kept as a fallback so running this suite on gpupc still works.

The gpupc path used to be the *only* default. That was correct while gpupc
was also the dev machine; since 2026-09-07 it only runs production and the
dev machine is elsewhere, so a home-relative default is the one that works
without per-machine configuration.

Never paste the password into a conversation, commit, or this file. See
`helpers.ts::loadProdCreds` for the exact resolution order.

## Minting an access token (`get-token.spec.ts`)

`get-token.spec.ts` logs in and hands back a real production JWT for the debug
account — useful for driving the API by hand with `curl`. It is **skipped by
default**: `npm run e2e:prod` runs every spec in this directory, and a second
login on every walkthrough only to mint a credential nobody here consumes is
cost without a reader.

```bash
# writes to /media/heygo/program/datahub/nous/secrets/claude-debug.token
E2E_MINT_TOKEN=1 npx playwright test --config e2e-prod/playwright.config.ts get-token

# or choose the path
E2E_MINT_TOKEN=1 E2E_TOKEN_OUT=/tmp/nous.token \
  npx playwright test --config e2e-prod/playwright.config.ts get-token

# hosts come from frontend/.env.production (VITE_API_URL / VITE_API_FALLBACK_URL)
curl -H "Authorization: Bearer $(cat /tmp/nous.token)" https://cn.nous.ink:88/api/v1/auth/me
```

**The token is never printed.** It goes to the file at mode `0600` (the file is
unlinked first, so a re-mint cannot inherit looser permissions from a
leftover); stdout gets only the path and the `exp` claim as an ISO timestamp. A
JWT echoed to stdout ends up in scrollback, CI logs and any transcript of the
run — a bearer credential for the real stack, copied into places nobody chose.
Delete the file when you are done with it.

## Target data — the owner's real project (default)

The walkthrough runs against **the owner's own production project**
(个人项目测试 1), reached by the debug account through a standing
`project_members` viewer row:

| | id |
|---|---|
| team (URL scope segment) | `personal` (a literal, not a snowflake — the personal-project URL convention for `projects.team_id IS NULL`) |
| project (个人项目测试 1) | `291022264100262` |
| episode (Ep1) | `324362669885098` |
| scene | `324838427143194` |
| shot | `325601447269110` |

All five are overridable via `PROD_TEST_TEAM_ID` / `PROD_TEST_PROJECT_ID` /
`PROD_TEST_EPISODE_ID` / `PROD_TEST_SCENE_ID` / `PROD_TEST_SHOT_ID` — see
`helpers.ts`.

### Why the owner's real project, not a purpose-built fixture

A QA fixture is created by whoever writes the test, so it is shaped the way
the test author already imagines the data looks. That is exactly the blind
spot this suite exists to cover. Aiming the same walkthrough at the owner's
real project — same assertions, same code, different data and a different
permission path — immediately surfaced three defects that the green fixture
run could not:

| defect | why the fixture run was blind to it |
|---|---|
| #1816 — personal-project read gates ignored explicit `project_members` rows, 403ing scripts/scenes/shots/beats | the fixture project is OWNED by the test account, so it never took the invited-member code path |
| #1817 — `GET /projects` lists only projects you OWN, so a shared project's URL never mounted the workspace at all | same: an owned project is always in the list, so the by-id fallback was never needed |
| #1820 — a persisted viewport framing empty space renders the canvas blank (viewport culling), and a viewer's autosave 403 loops on "Save failed" | the fixture canvas was created with a default viewport that happens to frame its nodes; only a real, panned canvas drifts out of frame |

The permission path matters as much as the data: running as an invited
viewer exercises the read gates and the read-only save path, which an
owner-only run structurally cannot reach.

**Prerequisite:** the debug account needs a `project_members` row on the
target project (`role='viewer'` is enough, and is the right level — the
walkthrough is read-only). Without it the run fails at the workspace-shell
assertion. If a run suddenly fails there, check that row still exists
before suspecting the code. The earlier QA-fixture target
(`331438215859255` / `337650825568029` / `337650825711390` /
`337650952269612` / `337650953731886`, owned by the debug account itself)
still exists and can be selected via the env overrides if you ever need a
target that depends on no grants at all.

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
5b. **Asset card on the canvas (P4)** — an asset-library reference is a node
   type now, and it is the P4 surface most exposed to the 2026-08-12 failure
   mode: each card renders from its OWN detail fetch, so a broken scope leaves
   a card that is present and blank. The board may legitimately hold no asset
   card, so the assertion is a disjunction (an asset card is visible, or the
   canvas says it is empty, or the ordinary nodes of step 5 are what this board
   holds). Which branch was taken is written into the run as a `p4-asset-card`
   ANNOTATION rather than swallowed — a step that cannot fail must at least
   report that it checked nothing, which is the same rule the readiness probes
   follow. To make it a real check, put one asset card on the fixture episode's
   canvas.
6. **Shot List tab, view three** — the flat per-shot table, seeded shot row
   visible.
7. **Script module** — the inline-mounted editor shell loads.
8. **Project materials (P3)** — the sidebar's `Characters` module opens the
   project's view over the ASSET LIBRARY (`GET /api/v1/projects/{id}/assets`),
   which replaced the project-local `project_characters` /
   `project_lib_entities` tables. Asserts the panel is visible and its linked
   COUNT rendered — nothing about the contents: a project with nothing linked
   is a legitimate state. The count is the falsifiable half, because the
   panel's failed-load branch renders `project-assets-error` INSTEAD of it;
   a visible count therefore separates "mounted and empty" from "the
   project-scoped read failed".
9. **Generated inbox (P1)** — the Library rail's `Generated` entry is
   clickable and the view mounts against the real `/api/v1/generated`, with
   its heading and tablist visible. Deliberately asserts NOTHING about the
   contents: this account's inbox may legitimately be empty, and a card
   assertion would turn "nothing generated lately" into a red deploy.
10. **Assets codex (P2)** — the Library rail's `Assets` entry is clickable and
   the shelf mounts against the real `/api/v1/assets`, with its heading and
   the `All` type tab visible. Same "no claim about contents" rule as step 9.
   The rail entry is addressed by its exact accessible name. The chevron
   beside it is named `Expand Assets` (`resources.assetsExpand`), so the two
   controls are distinguishable by name alone — no `.first()`. If a future
   change reintroduces two buttons with the same name here, the fix is to
   rename one of them, not to reach for a positional locator.

## Troubleshooting: "waiting for element to be visible, enabled and stable"

If a click times out with that message while the element is clearly fine
(resolved, `opacity:1`, unmoving, nothing covering it, and `force: true`
would have worked), **do not go looking for a jittery animation in the
product.** On the release host this symptom came from the browser, not the
page: headless Chromium was producing no frames at all — `requestAnimation
Frame` never fired, `document.timeline.currentTime` stayed at `0`, CSS
animations were frozen, and `page.screenshot()` hung to its timeout.
Playwright's "stable" gate compares an element's box across two consecutive
rAF callbacks, so with no frames *every* `click()` in *any* suite fails
that way.

The fix is the two Chromium launch args in `playwright.config.ts`
(`--disable-gpu --disable-software-rasterizer`) — neither works alone; the
comment there has the measurements. The spec now asserts frame production
up front, so this failure mode announces itself instead of being blamed on
a button.

⚠️ The same host breakage hits the repo's stubbed suite (`npm run
test:e2e`, root `playwright.config.ts`) identically — that config has *not*
been changed here. It is not a CI gate (`ci.yml` deliberately installs no
Playwright browsers), so the blast radius is local runs only, but expect
the same misleading timeout there until the same two args are added.

## Extending it

Keep it read-only. If a future check needs to verify a write path, that's a
different, more careful walkthrough (or a synthetic-data cleanup story) —
don't bolt writes onto this one silently.
