# Process Supervision

How MediaHub stays running across crashes, hangs, OOMs, and host
reboots. Five concentric layers, each catching a failure mode the
inner one misses.

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: NAS host auto-start (DSM at boot)                  │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: docker daemon restart=unless-stopped               │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: autoheal sidecar (kills hung containers)          │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: docker init (tini) — signal forwarding + reap zomb│
├─────────────────────────────────────────────────────────────┤
│ Layer 1: in-process — atexit + signal handlers + PR_SET_PD  │
└─────────────────────────────────────────────────────────────┘
```

Plus a **dev-only** Layer that's separate from prod:

  - `scripts/dev-backend.sh` — uvicorn supervisor that polls /health
    and `kill -9` + respawns when it flatlines. Equivalent of Layer 3
    for local dev where docker isn't in the picture.

## Layer 1 — in-process

**File**: `app/agent_framework/process_lifecycle.py`

Catches: normal exit (sys.exit), SIGINT (Ctrl-C), SIGTERM (`docker stop`).
Cleans up:
- `subprocess_registry` (yt-dlp / ffmpeg / whisper PIDs we tracked)
- `multiprocessing.active_children()` (any pool workers)

**Does NOT catch**: SIGKILL (kill -9), OOM kill, segfault. Those skip
all atexit hooks.

For SIGKILL/OOM, R2 added `safe_popen_kwargs()` which sets
`PR_SET_PDEATHSIG=SIGKILL` on Linux subprocesses. The kernel kills the
child the instant the parent dies, regardless of how the parent died.
This is the only way to defend against the parent SIGKILL case.

## Layer 2 — docker init (tini)

**Config**: `init: true` on each service in docker-compose.yml.

When the container starts, tini becomes PID 1 instead of uvicorn.
Tini's job:
- Forward signals from `docker stop` to the actual app
  (without it, Python's signal handlers don't fire because Python is
  not PID 1 and POSIX signal semantics for PID 1 are weird)
- Reap zombie processes (`<defunct>` rows that pile up when subprocess
  parents don't `wait()`)

## Layer 3 — autoheal sidecar (the missing piece, now added)

**Service**: `autoheal` in docker-compose.yml (image `willfarrell/autoheal`).

Catches the failure mode that Layer 4 doesn't: **process is alive but
hung**. Specifically: a DBOS workflow throws an exception that wedges
uvicorn's event loop. The Python process doesn't exit, the TCP port
stays open, but `/health/deep` starts returning `unhealthy` (or never
returns at all).

How it works:
1. Every 10s, autoheal queries the docker daemon for containers with
   label `autoheal=true`
2. Reads each one's docker healthcheck status
3. If status is `unhealthy` (3+ consecutive failed probes), SIGKILLs the
   container
4. `restart: unless-stopped` policy on that container picks up the
   SIGKILL and restarts within ~5s

Total time from "hang starts" to "back online": ~30s
(3× 30s healthcheck interval + 10s autoheal poll + ~5s restart).

**Tuning**:
- `AUTOHEAL_INTERVAL=10` — polls every 10s
- `AUTOHEAL_START_PERIOD=90` — won't act on freshly-started containers
  until 90s in (must exceed our backend's `start_period: 60s`)
- `AUTOHEAL_DEFAULT_STOP_TIMEOUT=15` — wait 15s for graceful stop
  before forcing kill

To opt a service in: add `autoheal=true` to its labels. To opt out: no
label.

**Privilege**: autoheal needs the docker socket (`/var/run/docker.sock`)
to inspect + restart peers. This is effectively root-on-the-daemon.
Acceptable here because (a) the image is small and pinned, (b) it's
first-party, (c) the scope is narrow (only restarts containers we
explicitly opt in via label).

## Layer 4 — docker restart policy

`restart: unless-stopped` on every service. Triggers on:
- Process exits with any code
- SIGKILL (whether autoheal-induced or external)
- Container crash (segfault, OOM)
- Host reboot — restarts containers that were running pre-reboot

Does NOT trigger on `docker stop` (operator intent) or `docker compose
down`.

## Layer 5 — NAS host auto-start

DSM (Synology) is configured to start the docker package at boot, which
in turn brings up the docker-compose stack. Manual intervention only
needed for power loss + filesystem fsck scenarios.

---

## Failure mode matrix

| Failure | Layer 1 (atexit) | Layer 2 (tini) | Layer 3 (autoheal) | Layer 4 (restart) | Layer 5 (NAS boot) |
|---|---|---|---|---|---|
| Clean shutdown | ✅ | ✅ | — | — | — |
| `docker stop` (SIGTERM) | ✅ | ✅ | — | — | — |
| Python exception → process exit | — | — | — | ✅ | — |
| OOM killer | ❌ | — | — | ✅ | — |
| `kill -9` (SIGKILL) | ❌ | — | — | ✅ | — |
| **DBOS event-loop wedge** (process alive, hung) | ❌ | — | **✅** | (via autoheal SIGKILL) | — |
| Container crash (segfault) | — | — | — | ✅ | — |
| Host reboot | — | — | — | — | ✅ |
| Subprocess orphaned by parent SIGKILL | **✅ (PR_SET_PDEATHSIG)** | — | — | — | — |

Every row has at least one ✅ — no failure mode goes unhandled.

## Dev environment (separate, no docker)

```bash
./scripts/dev-backend.sh    # foreground supervisor
```

Implements Layer 3-equivalent for `uv run uvicorn`: polls /health every
30s, after 3 consecutive failed probes does `kill -9` and respawns the
worker. Same wedge-detection logic, just at the process level instead
of the container level.

## What if autoheal itself dies?

`restart: always` on the autoheal service (stronger than
`unless-stopped` — even survives `docker stop autoheal`). And nothing
inside autoheal calls our app, so its scope of failure is narrow:
worst case the supervision layer is offline temporarily, the app keeps
running on its own restart policy.

If autoheal AND mediahub both die simultaneously and stay down:
docker daemon's restart loop picks up mediahub independently. No
single point of failure.
