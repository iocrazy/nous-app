# Deploying compose-config changes (services, env vars, volumes)

Watchtower keeps the **image** of running containers up to date. It does NOT
read `docker-compose.yml` and apply config changes (new services, changed env
vars, changed volume mounts, changed labels). When a backend PR modifies
`docker/docker-compose.yml`, the merged change has **no effect on prod until
someone runs `docker compose up -d` on the NAS**.

## Symptoms when this is missed

The actual prod incident that surfaced this gap (2026-05-10):

- PR #172 added `MEDIAHUB_ROLE=gateway` env var on the `mediahub` service +
  added a new `mediahub-worker` service.
- Watchtower pulled the new image, recreated the `mediahub` container — but
  with the **container's existing env vars** (no `MEDIAHUB_ROLE`). So
  `process_role` stayed `combined` instead of becoming `gateway`.
- The `mediahub-worker` service was never created at all (Watchtower has no
  concept of "compose service").
- The `mediahub-admin` service was unaffected at the time, but later went
  down for an unrelated reason and **couldn't restart** because nobody had
  reconciled compose state on the NAS.

User-visible outcome: admin端 (192.168.50.9:3097) "打不开"; backend
appearing not to take new env vars.

## Procedure (the only one)

```bash
ssh user@nas-ip -p 2222
cd /volume1/docker/mediahub/docker

# 1. Pull the latest config (includes the merged docker-compose.yml change)
git pull origin master

# 2. Apply it. This recreates ANY container whose config drifted from the
#    compose file — image tag, env var, label, volume, depends_on, etc.
sudo docker compose up -d

# 3. Verify the things you expected to change actually changed.
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker inspect mediahub-app-backend | jq '.[0].Config.Env' | grep MEDIAHUB_ROLE
```

`docker compose up -d` is idempotent — services whose config matches won't be
touched. Only drifted containers get recreated. Brief outage per recreated
service (~30-60s).

## When you need to run this

Any merge to master that touches `docker/docker-compose.yml`, including:

- Adding or removing a service
- Changing `environment:` (incl. adding new env vars even if they have a sane default in code)
- Changing `volumes:` mounts
- Changing `ports:` mappings
- Changing `depends_on:`
- Changing `healthcheck:` (Watchtower will pick this up too eventually but explicit `up -d` is faster)

When you only change application code (no compose changes), Watchtower
handles it cleanly — no manual step needed.

## Why we don't auto-run `docker compose up -d` on every deploy

- Adds ~30-60s of downtime to every release, even pure code changes that
  Watchtower can hot-swap in seconds.
- Requires the deploy pipeline to have `docker` socket access on the NAS,
  which means storing SSH keys / sudo passwords in CI secrets — a bigger
  attack surface than the current Watchtower webhook.
- Most deploys don't change compose config; the rare ones that do are
  worth the manual gate.

If we ever change this calculus, the place to wire it is
`.github/workflows/deploy-backend.yml`'s `Trigger Watchtower update` step
(replace with SSH + `docker compose up -d`).

## Future safety nets (P2)

- A check in CI that detects `docker/docker-compose.yml` changes in the diff
  and posts a PR comment / commit message tag like `[compose-up-required]`
  so reviewers can't miss it.
- A startup health probe in the backend that asserts expected env vars are
  set; a missing `MEDIAHUB_ROLE` (when the file says it should be gateway)
  flips an /api/v1/health/deep field operators can monitor.

## Related

- [#172 PR — gateway/worker split](https://github.com/iocrazy/mediahub/pull/172)
- [#176 PR — workflow health classifier](https://github.com/iocrazy/mediahub/pull/176)
  (this is what triggered the executor cascade that masked the
  config-drift issue)
- [`docker/docker-compose.yml` ⚠️ DEPLOYMENT GOTCHA section](../docker/docker-compose.yml)
