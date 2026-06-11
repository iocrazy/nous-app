#!/usr/bin/env bash
#
# mediahub container healer — closes the Watchtower-autonomous-recreate gap.
#
# THE PIT (hit 2026-06-01/02, manual `docker start` each time):
#   prod backend is a gateway/worker split (mediahub-app-backend +
#   mediahub-app-worker). Watchtower's stop→rm→create→start sequence can race
#   and leave a container in `created` (never started) or `exited` (explicit
#   SIGTERM). Neither self-heals:
#     - `restart: unless-stopped` does NOT restart a `created`-but-never-started
#       container, nor one that exited via an explicit stop.
#     - willfarrell/autoheal (mediahub-autoheal) only restarts `unhealthy`.
#     - the deploy-backend.yml guard (#350/#423/#462) only runs on CI-triggered
#       deploys, so a Watchtower AUTONOMOUS recreate has no catch.
#
# THE FIX: a standalone supervised sidecar that polls those two containers and
# `docker start`s any that sit in `created`/`exited`. It only ever drives them
# toward "running" — the same end state the deploy guard wants — so racing an
# in-flight deploy is harmless. It does NOT touch crash-looping containers
# (those show `restarting`, handled by the restart policy).
#
# SECURITY: mounts /var/run/docker.sock (root-equivalent), identical to the
# existing willfarrell/autoheal sidecar already running on this host.
#
# INSTALL (idempotent — re-run to update):
#   On the NAS:  sudo DOCKER=/usr/local/bin/docker bash container-healer.sh
# REMOVE / DISABLE (e.g. to deliberately stop a container for debugging):
#   docker rm -f mediahub-container-healer
#
set -euo pipefail

DOCKER="${DOCKER:-docker}"
HEAL_TARGETS="${HEAL_TARGETS:-mediahub-app-backend mediahub-app-worker}"
POLL_SECONDS="${POLL_SECONDS:-60}"
IMAGE="${IMAGE:-docker:cli}"

"$DOCKER" rm -f mediahub-container-healer >/dev/null 2>&1 || true

"$DOCKER" run -d \
  --name mediahub-container-healer \
  --restart unless-stopped \
  -e "HEAL_TARGETS=$HEAL_TARGETS" \
  -e "POLL_SECONDS=$POLL_SECONDS" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  "$IMAGE" \
  sh -c '
    echo "[healer] started; targets=$HEAL_TARGETS poll=${POLL_SECONDS}s"
    while true; do
      for c in $HEAL_TARGETS; do
        st=$(docker inspect -f "{{.State.Status}}" "$c" 2>/dev/null || echo missing)
        if [ "$st" = "created" ] || [ "$st" = "exited" ]; then
          echo "[healer] $(date -u +%Y-%m-%dT%H:%M:%SZ) $c is $st -> docker start"
          docker start "$c" >/dev/null 2>&1 || echo "[healer] start $c FAILED"
        elif [ "$st" = "missing" ]; then
          # 2026-06-11 mode 4: compose force-recreate renamed the old
          # container to <hash>_$c, then lost the canonical name to a racing
          # Watchtower recreate and aborted — no container holds the
          # canonical name, only a temp-named survivor in Created. Rename it
          # back and start it: a previous-version container running beats no
          # container (the deploy guard / Watchtower re-syncs the image).
          tmp=$(docker ps -a --filter "name=_$c" --format "{{.Names}}" | head -1)
          if [ -n "$tmp" ]; then
            echo "[healer] $(date -u +%Y-%m-%dT%H:%M:%SZ) $c missing; restoring temp-named $tmp"
            docker rename "$tmp" "$c" >/dev/null 2>&1 \
              && docker start "$c" >/dev/null 2>&1 \
              || echo "[healer] restore of $tmp FAILED"
          fi
        fi
      done
      sleep "$POLL_SECONDS"
    done
  '

echo "Installed mediahub-container-healer. Tail logs: $DOCKER logs -f mediahub-container-healer"
