# Watchtower configuration runbook

Last reviewed: 2026-05-12

## TL;DR

**Symptom that motivated this doc:** Today (2026-05-12) the deploy
workflow reported every PR's deploy as ✅ success. In reality the
Watchtower webhook returned 200/504 without actually being a real
Watchtower HTTP API — the new image stayed in ACR while prod kept the
previous one. Hours wasted debugging "downloads broken" while looking
at the wrong version of the code.

**Fix in this repo:** PR #253 added `GET /api/version` on backend +
a CI "Verify deploy" step that polls it post-trigger and fails CI
loudly if prod doesn't pick up the new SHA.

**Fix on NAS:** apply the compose snapshot in `deploy/nas/watchtower-docker-compose.yml`
(POLL_INTERVAL=300, real HTTP_API_TOKEN, port 8083 exposed). Until you
do, the CI verify step is the only thing catching deploy regressions —
which is fine but means every problematic deploy fails CI for 10 min
before it tells you about it.

## How the deploy chain actually works (today)

```
git push master → GH Actions deploy-backend.yml
  └─ Build image
  └─ Push to ACR (crpi-eat03wohif79y6f2.cn-shanghai.personal.cr.aliyuncs.com)
  └─ Push tags: `latest` + full git SHA
  └─ Trigger Watchtower webhook  ← step is best-effort; failure ignored
  └─ Verify deploy (PR #253)     ← polls /api/version, fails CI on timeout

Watchtower on NAS (heygo_watchtower):
  └─ Periodic poll (default 1h — see "Known issues" below)
  └─ Pull image if newer digest on ACR
  └─ Restart container with NEW image (not the cached one)
```

## Known issues with the current NAS Watchtower config

### 1. POLL_INTERVAL=3600 → ~1h worst-case deploy latency

Live compose has `WATCHTOWER_POLL_INTERVAL=3600`. So between a CI push
and the new image actually running, you can wait up to 1 hour if the
webhook isn't truly working (see #2). When two PRs land back-to-back,
the second one might miss the next poll window and wait the full hour.

**Fix:** `deploy/nas/watchtower-docker-compose.yml` sets this to 300
(5 min). The CI verify step has a 10 min budget, so 5 min cadence
gives ≤2 polls within the budget — comfortable headroom.

### 2. No HTTP API token / port → webhook is a no-op

The live compose does NOT include `WATCHTOWER_HTTP_API_TOKEN` or expose
a port. So the GH Actions `WATCHTOWER_URL` is hitting *something else*
(possibly the Synology DSM 8080 fallback page, which returns 200 for
some paths). CI sees 200 and claims success.

**Verify:** SSH to NAS and try a real Watchtower API call:

```bash
ssh nas 'curl -sS -m 5 -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer x" \
  http://localhost:8083/v1/update'
```

If this returns `200` it means an actual Watchtower HTTP API is up
(401/403 if the token is wrong but the API exists). If `503` /
connection refused / Synology DSM HTML, the API is NOT exposed.

**Fix:** apply `deploy/nas/watchtower-docker-compose.yml` (see
"Applying the upgrade" below).

### 3. Race when two PRs land within poll interval

When both PRs build to ACR at ~the same time, Watchtower's next poll
sees only the latest `:latest` tag and pulls that. The earlier PR's
image (still tagged with its own SHA) is on ACR but never pulled,
because we only restart on `:latest` changes.

This usually doesn't matter — the LATER PR's image already includes
the EARLIER PR's commits (they merged sequentially to master). But if
the earlier PR build is still in progress when the later PR's push
fires, you can end up running an image that doesn't include either
PR's diff cleanly.

**Mitigation:** the existing `concurrency: group=deploy-backend,
cancel-in-progress: false` in deploy-backend.yml serializes builds.
But Watchtower's poll-based pulls aren't serialized against builds.
Best fix is to gate on the verify step from PR #253 — if it doesn't
see the right SHA in 10 min, CI fails loudly and the operator knows.

## Applying the upgrade

**Pre-flight:**

```bash
# Generate a random API token (one-time)
TOKEN=$(openssl rand -hex 32)
echo "New WATCHTOWER_HTTP_API_TOKEN: $TOKEN"
```

**On NAS:**

```bash
# 1. Add token to the compose stack's .env
ssh nas 'sudo tee -a /volume1/docker/portainer/data/compose/2/.env' <<EOF
WATCHTOWER_HTTP_API_TOKEN=<paste-token-here>
EOF

# 2. Back up live compose
ssh nas 'sudo cp /volume1/docker/portainer/data/compose/2/docker-compose.yml{,.bak-$(date +%Y%m%d)}'

# 3. Copy upgraded compose
scp deploy/nas/watchtower-docker-compose.yml \
  nas:/tmp/watchtower-docker-compose.yml
ssh nas 'sudo cp /tmp/watchtower-docker-compose.yml \
  /volume1/docker/portainer/data/compose/2/docker-compose.yml'

# 4. Recreate container — `restart` is NOT enough; env + ports changed
ssh nas 'cd /volume1/docker/portainer/data/compose/2 && \
  sudo docker compose up -d watchtower'

# 5. Verify HTTP API is reachable
ssh nas "curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer \$WATCHTOWER_HTTP_API_TOKEN' \
  http://localhost:8083/v1/update"
# Expected: 200 (triggers an immediate poll cycle)
```

**In GitHub repo settings:**

Update the secrets (Settings → Secrets and variables → Actions):

- `WATCHTOWER_URL` → `http://<nas-public-or-tunnel>:8083/v1/update`
- `WATCHTOWER_TOKEN` → same value as `WATCHTOWER_HTTP_API_TOKEN` above

If you'd rather not expose 8083 directly to the public internet, route
it through Cloudflare Tunnel like the other NAS services
(`mediahubserver.heygo.cn`).

## Adding a new auto-update target

By default Watchtower only updates containers that opt in. To enroll
a new service, add this label to its compose:

```yaml
services:
  my-new-service:
    # ...
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

Then on NAS: `sudo docker compose up -d my-new-service` (recreate so
the label takes effect).

**Do NOT enroll:**

- `mediahub-app-redis` — auto-restart loses in-memory queue state
- `mediahub-sb-prod-*` — Supabase stack has its own update cadence;
  managed via `sb-prod` compose dir
- `mediahub-sb-dev-*` — ditto
- `nginx-hls` — host-network mode; restart drops live HLS sessions

## When CI verify fails

If the deploy workflow's "Verify deploy" step fails (introduced in
PR #253):

1. **Check the GH Actions log** for the last "attempt N: prod still on
   '<sha>'" line — that tells you what version is actually running.

2. **If "prod still on <unreachable>"** → the public URL isn't
   responding. Backend may be down (check `/health` directly) or
   Cloudflare tunnel may be flapping.

3. **If "prod still on <old-sha>"** → Watchtower didn't pick up the
   new image within 10 min. Force pull + recreate manually:

   ```bash
   ssh nas '
     sudo docker pull crpi-eat03wohif79y6f2.cn-shanghai.personal.cr.aliyuncs.com/heygo/mediahub-backend:latest
     cd /volume1/docker/mediahub/docker
     sudo docker compose up -d mediahub
   '
   ```

   Then investigate why Watchtower missed it — usually one of:
   - POLL_INTERVAL not yet shortened (apply this runbook)
   - Webhook URL wrong (test with the curl from "Pre-flight" above)
   - ACR auth failed on Watchtower side (check `docker logs heygo_watchtower`)

## See also

- `deploy/nas/watchtower-docker-compose.yml` — target compose state
- `deploy/nas/docker-compose.yml` — mediahub stack compose (separate)
- `docs/runbook/compose-config-changes.md` — why compose env changes
  need a full `docker compose up -d`, not just `restart`
- `.github/workflows/deploy-backend.yml` — the build + verify pipeline
