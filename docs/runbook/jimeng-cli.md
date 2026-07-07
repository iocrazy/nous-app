# Runbook: dreamina (即梦) CLI provider

The `JimengCliProvider` (backend) drives the official **dreamina** AIGC CLI as a
subprocess for image + video generation. The binary is baked into the backend
image; the only manual step is a **one-time OAuth login on the NAS** so the CLI
has a subscription session to spend credits against.

## What ships in the image

`Dockerfile` installs the CLI in the final stage:

```dockerfile
RUN DREAMINA_INSTALL_DIR=/usr/local/bin bash -c 'curl -fsSL https://jimeng.jianying.com/cli | bash' \
    && command -v dreamina \
    && dreamina --help >/dev/null
```

- The official installer auto-detects the platform and drops a single static
  binary. `DREAMINA_INSTALL_DIR=/usr/local/bin` pins it onto `PATH` so the
  non-root `app` user can run it.
- The build **fails hard** if the binary isn't on `PATH` and runnable — a
  missing CLI must break the image, never surface silently at runtime.
- **Architecture:** the installer fetches the binary for the *build* platform.
  CI builds `linux/amd64`, so the NAS running the image must be amd64 (the
  current Synology host is). If the NAS is ever arm64, rebuild there or add a
  buildx arm64 target.
- **CI reachability caveat:** the binary comes from a ByteDance CN CDN
  (`lf3-static.bytednsdoc.com`). A CI runner without CN egress will fail this
  layer. Verified reachable + installing cleanly in `python:3.13-slim` on
  2026-07-07. If CI can't reach it, mirror the binary to Aliyun OSS and point
  `DREAMINA_INSTALL_DIR` install at the mirror.

## Auth / credential persistence

The CLI writes its OAuth/login state under `$HOME/.dreamina_cli`. The container
runs as user `app` with `HOME=/app`, so login state lives at
`/app/.dreamina_cli`, persisted by the **`dreamina-auth` named volume**
(`docker/docker-compose.yml`), mounted on **both** the backend and worker
services. Login once; both read the same session.

> The exact login-state filenames under `/app/.dreamina_cli` are created by the
> first `dreamina login` (the installer only seeds `version.json` + the SKILL
> doc). **Verify after first login** (see below) and note the files here.

## First-time setup on the NAS

Any compose change (this PR adds the `dreamina-auth` volume) requires a manual
`docker compose up -d` on the NAS — **Watchtower does NOT read compose**, it
only pulls images and restarts with the container's existing config. See
[`compose-config-changes.md`](compose-config-changes.md).

```bash
ssh user@nas-ip -p 2222
cd /volume1/docker/mediahub/docker

# 1. Pull the new backend image + apply the compose change (creates the volume,
#    mounts it on backend + worker). Watchtower will NOT do this for you.
sudo docker compose pull mediahub mediahub-worker
sudo docker compose up -d

# 2. OAuth device-flow login (runs as the `app` user → writes to the volume).
#    The worker runs the generation workflows, so log in there.
sudo docker exec -it mediahub-app-worker dreamina login
#    → prints a verification_uri + user_code + device_code. Open the URL in a
#      browser, enter the code, authorize with the 即梦 subscription account.
#    For a fully headless flow:
#      sudo docker exec -it mediahub-app-worker dreamina login --headless
#      sudo docker exec -it mediahub-app-worker dreamina login checklogin --device_code=<code> --poll=30

# 3. Verify the session + see the credit balance.
sudo docker exec mediahub-app-worker dreamina user_credit

# 4. Confirm the login state landed on the shared volume, and record the paths.
sudo docker exec mediahub-app-worker sh -c 'ls -la /app/.dreamina_cli'
#    (optional, find anything the login just wrote:)
sudo docker exec mediahub-app-worker sh -c 'touch /tmp/marker && dreamina user_credit >/dev/null 2>&1; find / -name "*dreamina*" -newer /tmp/marker 2>/dev/null'
```

Because the volume is shared, the backend (gateway) sees the same session — its
health check (`JimengCliProvider.health()` → `dreamina user_credit`) should go
green immediately.

## Re-login / expiry

The OAuth session expires eventually. Symptoms: generation and health calls
return a `jimeng_not_logged_in` structured error (never a raw 500), and the
model's health badge shows a red dot.

```bash
sudo docker exec -it mediahub-app-worker dreamina relogin   # clear + fresh login
# or
sudo docker exec -it mediahub-app-worker dreamina login
sudo docker exec mediahub-app-worker dreamina user_credit   # confirm restored
```

No image rebuild or compose change is needed to re-login — the session lives in
the persistent volume.

## Credit exhaustion

`user_credit` returns the remaining balance; `no_credit` errors surface when the
subscription runs dry. Top up the 即梦 subscription account; no infra change
needed. Record the before/after `user_credit` delta to track per-generation
cost.

## Command reference (verified 2026-07-07)

```
dreamina login | relogin | logout | user_credit | version
dreamina text2image  --prompt=... --ratio=W:H --resolution_type=... --poll=N [--model_version=...]
dreamina image2image --images=<path> --prompt=... --poll=N
dreamina text2video  --prompt=... --model_version=seedance2.0fast --poll=N
dreamina image2video --image=<path> --prompt=... --model_version=... --poll=N
dreamina query_result --submit_id=<id> --download_dir=<dir>
dreamina list_task
```

stdout mixes human log lines with JSON — the provider scans for the JSON object
carrying `submit_id` / `gen_status` / `result_json` / `images` / `videos`.

> ⚠️ `image2video --image=` (singular) is **not yet live-verified** — the
> provider code and its tests currently lock the singular form. Before the
> PR-J2 video E2E, run `dreamina image2video -h` on the real CLI and reconcile
> the flag name (`--image` vs `--images`) across the provider + tests + this
> runbook.

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| Image build fails on the dreamina layer | CI can't reach the CN CDN, or NAS/build arch mismatch | mirror the binary (see arch/CI caveats above) |
| `jimeng_not_logged_in` on generate/health | OAuth session expired or never logged in | `dreamina relogin` on the worker |
| `no_credit` | subscription balance exhausted | top up the 即梦 account |
| Permission denied writing `/app/.dreamina_cli` | volume created before the image pre-created the dir | `sudo docker compose down && up -d` to repopulate the volume from the new image |
| Worker logs show a killed subprocess | generation exceeded the provider hard timeout | expected safety kill; retry, or raise the model's `--poll` budget |
