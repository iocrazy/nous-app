# `deploy/nas/` — NAS prod source-of-truth mirror

This directory tracks the **actual** docker-compose state running on the
NAS (`192.168.50.9`, path `/volume1/docker/mediahub/docker/`). It is NOT
the same as the repo's top-level `docker/docker-compose.yml` — that one
is a future-shaped reference; this one matches what is actually deployed.

## Why both files exist

Discovered 2026-05-10: NAS compose had drifted from `docker/docker-compose.yml`
in 4+ ways nobody noticed:

- Backend container named `mediahub` (not `mediahub-app-backend`)
- Backend listened on host port 8880 (not 8080)
- `MEDIAHUB_ROLE` env var entirely missing → process_role stuck at `combined`
- New `mediahub-worker` service from #172 never created (Watchtower can't add services, only restart existing ones)
- `mediahub-admin` container never deployed despite being in `docker/docker-compose.yml`

Result: `mediahub-admin` 192.168.50.9:3097 was 100% unreachable for an unknown duration.

## Two-file design

| File | Audience | Purpose |
|------|----------|---------|
| `docker/docker-compose.yml` | Future / new deploys | Aspirational + future-ready (gateway/worker split, autoheal sidecar, pre-bake DBOS env). Used by anyone setting up a fresh NAS. |
| `deploy/nas/docker-compose.yml` | Current prod NAS | Mirrors what's running RIGHT NOW. Updated whenever the NAS file changes. Source of truth for "what would `docker compose up -d` actually do on the NAS today". |
| `deploy/nas/.env.example` | Current prod NAS | Mirrors the env keys (no values) the NAS .env defines. Recovery from scratch checklist. |

## Sync procedure

When something on NAS changes that isn't already reflected here:

```bash
# 1. Pull the live state down
ssh nas 'sudo cat /volume1/docker/mediahub/docker/docker-compose.yml' \
  > deploy/nas/docker-compose.yml

# 2. Refresh the env shape (keys only, no values)
ssh nas 'sudo grep -E "^[A-Z_]+=" /volume1/docker/mediahub/docker/.env \
  | sed "s/=.*$/=<value>/"' > deploy/nas/.env.example

# 3. Diff against last commit
git diff deploy/nas/

# 4. Commit if change is intentional
git add deploy/nas/
git commit -m "ops: sync deploy/nas with NAS state — <reason>"
```

## When to update which file

- **Adding a new service / changing prod env / changing port** → update `deploy/nas/docker-compose.yml`, then SSH to NAS and apply (`docker compose up -d`). The repo file stays as-is unless you also want it in the future-ready spec.

- **Refactoring the future-ready spec** (gateway/worker split rework, etc.) → update `docker/docker-compose.yml`. NOT auto-applied to NAS — apply via the runbook in `docs/runbook/compose-config-changes.md`.

## Disaster recovery from scratch

If the NAS dies and you need to rebuild from a fresh Synology install:

1. Install ContainerManager package
2. Mount `/volume1/docker/mediahub/docker/` from backup or recreate
3. Copy `deploy/nas/docker-compose.yml` here
4. Populate `.env` from `deploy/nas/.env.example` keys + 1Password secrets
5. `cd /volume1/docker/mediahub/docker && sudo docker-compose up -d`
6. Verify all containers healthy + admin reachable at `192.168.50.9:3097`
7. Restore `/volume1/docker/datahub/mediahub-sb-prod/` and `mediahub-sb-dev/` similarly (those are separate Supabase stacks)

## Why we don't auto-sync

The NAS compose evolves through ad-hoc edits during incidents (today: I added `DBOS_DATABASE_URL` to the worker service via `sed` while debugging). Auto-syncing would either require:

- CI write access to NAS (security boundary)
- A NAS-side cron job that pushes to the repo (reverse direction; complex)
- Manual `git pull` on NAS + manual `docker compose up -d` (no different from now)

Manual reconciliation has the property that **every NAS change ends up in a git commit with a "why"**, which is more valuable than automation.

## See also

- `docs/runbook/compose-config-changes.md` — Watchtower vs `docker compose up -d` semantics
- `docs/runbook/asyncpg-canary.md` — DBOS / Supavisor connection knobs
- Memory file `reference_nas_ssh.md` — NAS SSH access + known gotchas
