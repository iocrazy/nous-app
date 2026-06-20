# NewsNow Self-Hosted (Topic Inspiration Data Engine)

NewsNow = ourongxing/newsnow (MIT). Ships 50+ platform trending-feed scrapers
out of the box. The Topic Inspiration module's `newsnow` adapter calls its
`/api/s?id=<platform>` JSON API to fetch ranked headlines.

## Deploy (NAS, one-time manual)

WARNING: Watchtower does not read compose changes (see CLAUDE.md and
docs/runbook/compose-config-changes.md). A newly-added service must be
activated manually.

SSH to NAS and run:

    ssh user@nas-ip -p 2222
    cd /volume1/docker/mediahub
    sudo docker compose -f docker/docker-compose.yml up -d newsnow

The container binds port 4000 on the host. It shares the compose default
network, so other MediaHub containers can reach it by service name.

## Backend Connectivity

Set this env var on the backend container so the newsnow adapter resolves
within the Docker network:

    NEWSNOW_API_URL=http://mediahub-newsnow:4000

On NAS the env lives in the bind-mounted file at
`/volume1/docker/mediahub/docker/.env` (not in docker-compose.yml
`environment:` — Watchtower does not apply those on restarts; see
reference_nas_backend_env.md).

After editing `.env`, restart the backend:

    sudo docker stop -t0 mediahub-app-backend
    sudo docker start mediahub-app-backend

For local dev, set in `backend/.env`:

    NEWSNOW_API_URL=http://localhost:4000

## Source Failures (platform scraper broken)

When a specific platform stops returning items, the upstream NewsNow community
typically patches the scraper within days. To pick up the fix:

Pull the new image and restart:

    sudo docker compose -f docker/docker-compose.yml pull newsnow
    sudo docker compose -f docker/docker-compose.yml up -d newsnow

Alternatively, if running from source instead of the prebuilt image:

    cd <newsnow-repo>
    git pull
    sudo docker compose -f docker/docker-compose.yml up -d --build newsnow

Source health per platform is visible in Settings > Source Management
(Phase 2 — dead sources shown in red).

## Verify

From the NAS host:

    curl http://localhost:4000/api/s?id=hackernews | head

Expected response:

    {"status":"success","items":[...]}

From inside another MediaHub container (using the service name):

    curl http://mediahub-newsnow:4000/api/s?id=hackernews | head

## Platform IDs (common)

| Platform     | id             |
|--------------|----------------|
| Hacker News  | hackernews     |
| GitHub       | github         |
| Weibo        | weibo          |
| Zhihu        | zhihu          |
| Bilibili     | bilibili       |
| Douyin       | douyin         |
| V2EX         | v2ex           |

Run `GET /api/sources` on the NewsNow container for the full list supported
by the installed image version.
