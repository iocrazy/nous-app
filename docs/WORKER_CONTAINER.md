# Worker container deployment (D10-A / S2)

The `feat/dbos-pr-d2` branch ships **physical Gateway/Worker split** as
opt-in. Default deploy (`MEDIAHUB_ROLE=combined` / template still
commented in docker-compose) keeps the single-process behavior — no
behavior change required.

This doc is the runbook for **enabling** the split.

## When to enable

You need this only if:
- Long worker tasks (transcription / video analysis) are stalling API
  request latency
- DBOS worker crashes are taking down HTTP serving with them
- You want horizontal scale-out of workers separately from gateway

For NAS-single-process deployments: keep combined mode.

## Steps

### 1. Set `MEDIAHUB_ROLE=gateway` on the existing `mediahub` service

In `docker/docker-compose.yml`, find the `mediahub` service `environment:`
block and add:

```yaml
    environment:
      - TZ=Asia/Shanghai
      - REDIS_URL=redis://redis:6379/0
      - MEDIAHUB_ROLE=gateway          # ← add this
      ...
```

After this, the `mediahub` container will:
- Serve HTTP API as before
- Init DBOS so dispatch (`start_workflow_routed`) still works
- **NOT** call `DBOS.launch()` — workflows queue but won't execute

### 2. Uncomment the `mediahub-worker` service block

In the same file, lines 66-90 contain a commented `mediahub-worker:`
template. Remove the `# ` prefix from each line.

After uncommenting, the worker container will:
- Run from the same image as gateway
- Set `MEDIAHUB_ROLE=worker` → call `DBOS.launch()`, run pool
- Skip HTTP API mounting (only serves `/health` + `/internal/bounds`)
- Share the same DBOS PG queue with gateway

### 3. (Removed in R1) Dispatch gate is always-on

After R1 the gate runs whenever the bounds registry has live entries.
Combined-mode self-registers all local workflows, so single-process
deploys always pass naturally. Split mode fails fast when no worker
advertises the requested workflow.

### 4. Restart

```bash
cd /volume1/docker/mediahub
sudo docker compose up -d --force-recreate mediahub mediahub-worker
```

## Verification

```bash
# Both containers healthy
curl http://localhost:8080/health        # gateway
curl http://gateway-host/api/v1/health/deep   # gateway view of bounds

# Worker should appear in bounds registry
# (Gateway logs will show: "Bounds: self-registered worker_id=...")
```

## Scaling out workers

```yaml
mediahub-worker:
  deploy:
    replicas: 3   # spawn 3 worker containers
```

Each worker registers its own bounds; gateway sees the full pool.

## Rollback

If something misbehaves:

1. Set `MEDIAHUB_ROLE=combined` on gateway (or unset)
2. Stop worker container(s)
3. `docker compose up -d --force-recreate mediahub`

(After R1 there is no env flag to flip — the gate self-disables when
the registry only contains the combined-mode self-bound, which by
definition advertises every local workflow.)

Result: combined-mode behavior restored, no data loss.

## Caveats

- Only enable after applying migration 187+ (the gateway probes for
  agent_commitments at startup; missing tables WARN but don't block)
- Multi-replica gateway requires Redis for shared state (BoundsRegistry +
  ModelHealth + LifecycleBus) — see I4/K3 RedisXxx wrappers
- Worker container needs same DOWNLOAD_HOST_PATH mount if it runs
  yt-dlp / transcribe workflows
