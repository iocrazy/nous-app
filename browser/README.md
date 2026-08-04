# nous-browser

Browser automation sidecar for the distribution **session channel**. It answers
one question in S1: *is this platform session still alive?*

Design doc: [`docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md`](../docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md)

## Security boundary

This service **never touches the database and never holds an encryption key.**
`nous-backend` decrypts `social_accounts.session_state` and posts the plaintext
`storage_state` over the internal network; this service computes an answer and
forgets it.

That boundary is load-bearing, and three rules keep it honest:

- Plaintext `storage_state` exists **in process memory only**. It is handed to
  `browser.new_context(storage_state=<dict>)` directly — never written to a
  temp file, never logged, never placed in an exception message. Playwright
  also accepts a *path* here; that overload must not be used.
- Every string leaving the process goes through `app/redaction.py::scrub`,
  which strips `user:pass@` out of any URL. The realistic leak is not the
  session blob, it is a proxy password embedded in a Chromium error string that
  someone copies into `message`.
- The service is not open on the internal network: every endpoint except
  `/healthz` requires `X-Internal-Token`, and an **unset** token means the
  service refuses all requests (503) rather than running unauthenticated.

The container mounts no volumes and is not published to the host.

## Why headed, with Xvfb

"Runs on a server" and "runs headless" are independent axes. This service is
**server-side and headed**, with Chromium drawing into an Xvfb virtual
framebuffer.

Headless Chromium — including `headless=new` — carries detectable traces
(`navigator.webdriver`, missing Chrome runtime objects, an odd WebGL renderer
string, empty font/plugin lists). Douyin checks. The observed failure is
specific and nasty: headless gets bounced from the upload page to the login
page, which reads as *"this cookie expired"*. Good accounts get condemned,
intermittently, and the user is told to re-scan a QR code that was never the
problem.

This is a **decided** item (design doc §2.4), not a tunable. `HEADLESS = False`
is a module constant in `app/browser_runtime.py`, not an environment variable,
and a test asserts it. The cost is a few dozen MB of image and slightly higher
per-context memory.

## Contract

### `GET /healthz`

No auth. Returns `200` when healthy, **`503` when degraded**, with the same body
either way:

```json
{"status": "ok", "browser_ready": true, "xvfb": true, "version": "0.1.0"}
```

`browser_ready` is a **real probe**: it launches a Chromium and reads its
version (result cached for `BROWSER_HEALTH_PROBE_TTL_S`, so a healthchecker does
not start a browser every 30s). It transitively covers Xvfb, since headed
Chromium cannot start without a working `DISPLAY`.

The 503 is deliberate. A body that says `degraded` while the HTTP status says
`200` is how this project already lost three days to a green `/health` sitting
on top of a dead engine.

### `POST /session/validate`

Header `X-Internal-Token: <BROWSER_INTERNAL_TOKEN>` — required.

```json
{
  "platform": "douyin",
  "storage_state": { "cookies": [], "origins": [] },
  "environment": {
    "proxy_url": null,
    "user_agent": null,
    "locale": "zh-CN",
    "timezone_id": "Asia/Shanghai",
    "geo_lat": null,
    "geo_lng": null
  }
}
```

`environment` is optional and every field may be null in S1, but the whole path
down to `browser.new_context()` is already wired, so S4 only has to start
sending values.

Response (`200`):

```json
{
  "success": true,
  "status": "session_valid",
  "message": "upload page reached with no login prompt",
  "detail": {"final_url": "...", "login_markers": [], "attempts": 1, "platform": "douyin"}
}
```

`status` comes from a shared, **platform-neutral** enum (design doc §7.8):
`session_valid`, `session_invalid`, `qrcode_expired`, `sms_required`, `timeout`,
`proxy_failed`, `failed`. Adding a platform must never add a status. This
endpoint emits five of them; `qrcode_expired` / `sms_required` belong to the S2
login flow.

`proxy_failed` is separated from `session_invalid` on purpose: conflating them
sends a user off to re-scan a QR code in order to fix a proxy outage. It is
raised only for Chromium's proxy-layer error codes, never guessed from a
timeout — a blackholing proxy and a slow platform look identical from here, so
a timeout stays `timeout` and `detail.proxy_configured` tells the caller whether
a proxy was in the path.

Other status codes:

| Code | When | Body |
|------|------|------|
| `401` | missing/wrong `X-Internal-Token` | FastAPI `detail` |
| `503` | `BROWSER_INTERNAL_TOKEN` unset (fails closed) | FastAPI `detail` |
| `422` | request body fails schema validation | FastAPI `detail` |
| `400` | `platform` is not implemented | **`SessionResult` shape**, `status: "failed"` |

The `400` keeps the response shape uniform so callers parse one thing, while
still not being mistaken for a verdict about the account.

## How session validation works

`app/platforms/douyin.py::validate_session` is the **only** Douyin session check
that may exist in this codebase — enforced by a test that greps `app/` for a
second definition. The reference project kept two copies; only one ever received
the headless fix, so its other path kept killing healthy accounts intermittently.

The three-piece set from the design doc (§7.1), and where each part lives:

| Piece | Where |
|-------|-------|
| Headed browser | `browser_runtime.HEADLESS = False` |
| Retry 3× | `validation.run_dom_session_validation` loop, bounded by `BROWSER_VALIDATE_ATTEMPTS` |
| Lenient judgement | `douyin.judge_douyin_session`, sampled after a settle |

Navigation uses `goto(..., wait_until="domcontentloaded")` followed by a fixed
settle, then reads `page.url`. It explicitly does **not** use
`wait_for_url(<exact url>, 5s)` — the page sometimes arrives late and sometimes
lands and then bounces, so a strict matcher produces false "expired" verdicts.
It also does not require an exact URL: query strings, fragments and the two
gray-released publish-page variants all count.

Judgement is strict in exactly one place: **URL parsing**. The logged-out
redirect keeps the upload path inside a `redirect_url` query parameter, so the
obvious `"content/upload" in page.url` substring test declares the *login page*
a valid session. `judge_douyin_session` checks the parsed host and path.

Login markers are checked for **visibility**, not presence: the authenticated
app shell keeps hidden login nodes in the DOM, so a `count()`-based check
reports a healthy session as logged out.

`judge_douyin_session` is pure — `(url, visible_login_texts) -> Judgement` — so
the part most likely to be wrong is table-tested without a browser.

### Bounds

Every loop is bounded; there is no `while True` anywhere (design doc §7.2),
including the Xvfb socket wait in `entrypoint.sh`. A validation call is capped
per-attempt *and* in total wall clock, and admission to the browser pool is
itself bounded, so a saturated service fails fast instead of queueing forever.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `BROWSER_INTERNAL_TOKEN` | *(none)* | Shared secret. **Unset ⇒ all authenticated requests 503.** |
| `BROWSER_PORT` | `8090` | Listen port |
| `DISPLAY` | `:99` | X display Xvfb owns |
| `XVFB_SCREEN` | `1920x1080x24` | Virtual framebuffer geometry |
| `BROWSER_NAV_TIMEOUT_MS` | `90000` | Per-navigation budget |
| `BROWSER_SETTLE_MS` | `2500` | Idle time before sampling the URL |
| `BROWSER_VALIDATE_ATTEMPTS` | `3` | Retries per validation |
| `BROWSER_VALIDATE_ATTEMPT_TIMEOUT_S` | `120` | Per-attempt ceiling |
| `BROWSER_VALIDATE_TOTAL_TIMEOUT_S` | `180` | Whole-request ceiling |
| `BROWSER_MAX_CONCURRENT` | `4` | Concurrent headed Chromium instances |
| `BROWSER_SLOT_WAIT_S` | `30` | Max wait for a free browser slot |
| `BROWSER_HEALTH_PROBE_TTL_S` | `60` | `/healthz` browser-probe cache TTL |

## Running locally

Docker is the supported path — it is the only way to get a matching Chromium
plus Xvfb.

`--shm-size` is **required**, not tuning. Docker defaults `/dev/shm` to 64MB,
which makes Chromium tabs crash at random ("Target closed"). The usual
workaround flag, `--disable-dev-shm-usage`, is deliberately not set — it would
move shared memory to `/tmp`, which in this volume-less container is real disk,
putting rendered content of logged-in pages there (see §7.6). Give it memory
instead. The compose service sets `shm_size: 1gb`; tmpfs allocates on demand, so
the ceiling costs nothing until used.

```bash
cd browser
docker build -t nous-browser:local .
docker run --rm -p 8090:8090 --shm-size=1g --init \
  -e BROWSER_INTERNAL_TOKEN=dev-token nous-browser:local

curl -s localhost:8090/healthz
curl -s -X POST localhost:8090/session/validate \
  -H 'X-Internal-Token: dev-token' -H 'Content-Type: application/json' \
  -d '{"platform":"douyin","storage_state":{"cookies":[],"origins":[]}}'
```

Bare-metal is fine for the API and the unit tests, but validation needs a
Playwright-supported host OS:

```bash
cd browser
uv sync --frozen --extra dev --no-install-project
.venv/bin/python -m playwright install chromium   # host OS must be supported
.venv/bin/python -m pytest -q
```

## Tests

```bash
cd browser
.venv/bin/python -m pytest -q          # unit suite, no browser needed
.venv/bin/python -m pytest -m integration    # needs a real Chromium; auto-skips otherwise
```

Integration tests skip themselves when Chromium is not installed on disk, so
they never block a machine without browsers. To run them for real, use the
container:

```bash
docker build -t nous-browser:test .
docker run --rm --shm-size=1g --init -e BROWSER_INTERNAL_TOKEN=dev-token nous-browser:test \
  sh -c 'Xvfb :99 -screen 0 1920x1080x24 & sleep 2; python -m pytest -m integration -q'
```

(The image omits `tests/` and dev extras, so this needs a build without the
`.dockerignore` exclusion, or a bind-mounted checkout.)

## Dependencies and upgrading Playwright

`uv.lock` is committed and the image installs from it with `uv sync --frozen`,
so a rebuild months from now produces the same environment. `--frozen` turns a
stale lock into a build error instead of a silent re-resolve.

This matters more here than in a typical service: a Playwright point release can
change the browser fingerprint and the DOM these validators read. Floating
versions produce the worst possible failure — no code changed, CI green, and
session validation quietly starts misjudging live accounts.

The `playwright` pin in `pyproject.toml` and the base image tag in `Dockerfile`
are **one decision in two files**. Bump them together, then re-run `uv lock`, or
the container fails at runtime with `Executable doesn't exist`.

## Known gaps / open questions

- **patchright vs playwright.** The reference project uses `patchright` (an
  anti-detection fork) in its `pyproject.toml` while its `requirements.txt`
  still pins stock `playwright` — two install paths yielding different runtimes.
  This service deliberately has a single source: stock `playwright`, plus
  `--disable-blink-features=AutomationControlled` and headed mode. No stealth
  shim is bundled. If Douyin starts rejecting sessions that a real browser
  accepts, evaluating patchright is the first move; the launch arguments are
  isolated in `browser_runtime` to make that a one-file change.
- **Proxy is applied at launch, not per context.** One browser per validation
  makes launch scope equal account scope, and it avoids Chromium's requirement
  that per-context proxying be declared globally up front. S6's long-lived
  browser model has to move this down to `new_context()`.
- **`/session/login/*` and `/publish` are not implemented** — S2 and S3.
- **No account-level serialisation yet** (design doc §7.5). Two concurrent
  sessions for one account can knock each other offline; the lock belongs on the
  backend side, which is the component that knows about accounts.
