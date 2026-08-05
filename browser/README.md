# nous-browser

Browser automation sidecar for the distribution **session channel**. It answers
two questions: *is this platform session still alive?* (S1) and *can a user bind
a new account by scanning a QR code?* (S2).

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

`status` comes from a shared, **platform-neutral** enum (design doc §7.8), and
this service carries the **whole table** even where it cannot yet produce a
member:

```
session_valid  session_invalid  proxy_failed  timeout  failed
waiting_scan   scanned  qrcode_expired  sms_required  success   published
```

Adding a platform must never add a status. `published` is defined here although
only S3 emits it — the two services keep independent copies of this enum (no
shared Python package across a container boundary), and a member missing on one
side means the peer's answer arrives unrecognised and gets flattened to
`failed`, discarding exactly the part that told the user what to do.

`/session/validate` emits the first five; the rest belong to the login flow.

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

### `POST /session/login/start`

Opens a browser on the platform's login page, grabs the QR code, and **leaves
the browser running**. All five login endpoints require `X-Internal-Token`.

```json
{"platform": "douyin", "environment": { … same shape as /session/validate … }}
```

```json
{
  "login_session_id": "3f2a…",
  "status": "waiting_scan",
  "qrcode_data_url": "data:image/png;base64,…",
  "expires_at": "2026-08-05T02:00:03.669683Z"
}
```

`expires_at` is a hard deadline, not a hint: the context is destroyed then,
scanned or not.

| Code | When | Body |
|------|------|------|
| `400` | platform has no login flow | `SessionResult` |
| `503` | concurrency ceiling reached (`Retry-After` set) | `SessionResult`, `detail.reason = "login_capacity"` |
| `502` | launch/navigation failed | `SessionResult`, `status` is `proxy_failed` / `timeout` / `failed` |

### `GET /session/login/{id}/status`

**One snapshot of the live page, returned immediately.** It never waits for the
user. The polling loop belongs to the backend workflow — the side that owns the
task record and writes heartbeats; an endpoint that blocked until someone
scanned would pin an HTTP worker for minutes and hide the wait from everything
that monitors it.

```json
{
  "status": "waiting_scan",
  "qrcode_data_url": "data:image/png;base64,…",
  "message": "QR code displayed",
  "detail": {"reason": "…", "platform": "douyin", "terminal": false,
             "expires_at": "2026-08-05T02:00:03.669683+00:00"}
}
```

Two properties callers can rely on:

- **`qrcode_expired` always arrives already refreshed.** The endpoint clicks the
  platform's own refresh affordance and returns the new code in the *same*
  response. If the refresh fails, `qrcode_data_url` is **null** rather than the
  dead code — null is how you tell "here is a new code" from "there is no usable
  code", and serving the stale one would have the user scan an image that cannot
  work.
- **`detail.terminal` says when to stop polling.** Without it the backend would
  need its own copy of which statuses are final, which is the kind of duplicated
  enum knowledge that drifts.

This route answers `200` even for typed failures, including a crashed page: a
poller must never read the HTTP status to find out what happened to the login.
`404` means the handle is genuinely gone — a session that merely *expired* is
still answerable for a grace period and reports `timeout`.

### `POST /session/login/{id}/sms`

`{"code": "123456"}` → `{"status": …, "message": …}`. Digits only, 4–8 long;
anything else is `422` before it reaches the page. If the page is not asking for
a code, the response says so and reports what it *is* asking for.

### `GET /session/login/{id}/state`

The sensitive one — plaintext `storage_state`, plus a best-effort profile:

```json
{"storage_state": {…}, "platform_user_id": "…", "username": "…", "avatar_url": null}
```

It re-judges the page first rather than trusting a previously recorded
`success`: this is the one call whose output gets persisted, and handing back a
half-finished session's cookies creates an account row that never works. Not
logged in yet ⇒ `409` + `SessionResult`, whose `status` says whether waiting
longer would help.

### `POST /session/login/{id}/close`

`{"closed": true}`. Idempotent. `404` for an unknown handle.

### `POST /session/publish`

Publishes one post through the platform's own web UI. Header
`X-Internal-Token` — required. **Minutes, not seconds**: a few hundred megabytes
of upload, a form that only renders once the transfer finishes, and a redirect
to wait on.

```json
{
  "platform": "douyin",
  "storage_state": { "cookies": [], "origins": [] },
  "environment": { "…": "same shape as /session/validate" },
  "intent": {
    "content_type": "video",
    "media": [{"kind": "video", "url": "http://nous-kong:8000/…",
               "filename": "clip.mp4", "content_type": "video/mp4",
               "size_bytes": 12345678}],
    "title": "Launch Day Recap",
    "description": "…",
    "topics": ["travel", "food"],
    "visibility": "public",
    "allow_download": true,
    "cover": null,
    "scheduled_at": null,
    "platform_options": {}
  }
}
```

The intent is stated in **channel** terms, never a platform's own vocabulary
(`visibility: "friends"`, not Douyin's native enum). Translating it is each
publisher's job (design doc §6.1a); anything only one platform understands goes
in `platform_options`, which is passed through untouched.

Response (`200`), a superset of `SessionResult` so a caller that only knows the
smaller shape still parses the four fields it cares about:

```json
{
  "success": true,
  "status": "published",
  "message": "video published",
  "detail": {"editor_variant": "version_2", "final_url": "…", "platform": "douyin"},
  "platform_item_id": null,
  "published_url": null,
  "updated_storage_state": {"cookies": [ … ]}
}
```

`status` is one of `published` / `session_invalid` / `timeout` / `proxy_failed` /
`failed` — a subset of the same platform-neutral enum, asserted by a test that
greps the publish modules for any status outside it.

**`updated_storage_state` is the field that matters most.** Platform sessions
slide forward on use: the server hands back refreshed cookies every time an
authenticated page loads. Dropping them means every publish spends down the
original grant instead of renewing it, turning a three-month session into a
two-week one — with **no error anywhere** (design doc §4.2 step 6). It is
therefore collected in a `finally`, so a publish that died at the last click
still returns the renewal it earned. `null` means no context ever got far enough
to have one; the field is always present, so a caller never has to tell "no
renewal" from "field missing".

**`platform_item_id` and `published_url` are null for Douyin.** Its
post-publish redirect lands on the content manager and carries no identifier for
the post just created. Reading the newest card off that list would be wrong for
any account with a scheduled or concurrently-published post, so the fields stay
null and `detail.final_url` carries what is actually known. Callers must tolerate
this rather than treat it as a failed publish.

Notable typed refusals:

| `detail.reason` | When |
|---|---|
| `scheduling_not_supported` | `scheduled_at` is non-null. Refused, **not** published immediately — a post that goes out twelve hours early has already been seen by the time anyone notices |
| `missing_video` / `too_many_videos` / `empty_title` | caught before any browser exists (§7.7) |
| `bad_video_url` / `unsupported_video_type` | ditto; `file://` is rejected at the pure layer so no code path opens a URL with one |
| `visibility_control_missing` | a **non-default** visibility had no control on the page. Refused rather than published with the platform default — see below |
| `sms_verification_required` | the platform demanded an SMS code to publish; this endpoint has no channel to supply one |
| `session_lost_during_publish` | bounced to a login screen mid-flight ⇒ `session_invalid`, not `timeout` |

| Code | When | Body |
|------|------|------|
| `400` | platform has no publisher | `SessionResult` shape |
| `401` / `503` | token missing / unset | FastAPI `detail` |
| `422` | body fails schema validation | FastAPI `detail` |

Pool saturation answers `200` with `failed` and `detail.error_kind =
"pool_saturated"` — back-pressure, not a verdict on the account. A publish holds
its browser slot for minutes, so saturation is ordinary and the caller should
requeue.

## Keeping a login alive without leaking browsers

A QR code is not data — it is a view of a live browser context, and the moment
that context dies the code is worthless. So login is the one flow here that
outlives its request, which makes leak prevention the central problem rather
than an afterthought. Three independent mechanisms, because the point of defence
in depth is that no single one has to be perfect:

1. **A hard deadline per session** (`BROWSER_LOGIN_TTL_S`, default 5 min), fixed
   at creation. The only renewal is a one-shot, bounded grace window granted
   when a login *succeeds*, so a scan landing at 4:58 still leaves time to
   collect `storage_state`.
2. **A background reaper**, which **closes the browser** — dropping the registry
   entry alone would leave a Chromium running with nothing pointing at it, a
   worse leak than the one being fixed.
3. **An inline deadline check on every request.** If the reaper task ever dies,
   sessions still expire, just lazily. Cleanup whose correctness depends on a
   background task staying alive is a pattern this project already has scars
   from.

Released sessions leave a **tombstone** for `BROWSER_LOGIN_TERMINAL_GRACE_S`, so
a caller polling once more gets a typed `timeout` instead of a bare 404 whose
meaning it has to guess.

Concurrency is capped by `BROWSER_LOGIN_MAX_SESSIONS`, refused with a typed
`503` *before* browser N+1 is launched. It is a **separate** pool from
`BROWSER_MAX_CONCURRENT`: a login holds its browser for minutes, and letting
logins draw from the validation pool would let three idle QR codes starve
session checks for a whole TTL.

Every page operation on a session is serialised behind a per-session lock with a
bounded wait — a status poll and a code submission overlap naturally, and
Playwright will happily interleave them into a page state neither asked for.

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

## How QR login works

Same split, same reason. `app/platforms/douyin.py` contributes selectors, marker
texts and two **pure** functions; `app/login.py` and `app/login_sessions.py` own
everything platform-neutral (driving Playwright, bounding waits, TTL, locking,
typed failures). Adding a platform is a module plus a `register_login()` call.

| Piece | Where |
|-------|-------|
| "What state is this page in?" | `douyin.judge_douyin_login` — pure, `LoginPageSnapshot -> LoginJudgement` |
| "Who just logged in?" | `douyin.parse_douyin_profile` — pure |
| Reading the page | `login.LoginDriver` |
| Keeping it alive, killing it on time | `login_sessions` |

### DOM notes (verified against the live page, 2026-08)

- **A visible verification-code field means nothing on Douyin's login screen.**
  The phone-login form renders *beside* the QR panel, so
  `input[placeholder*="验证码"]` is visible the entire time the code is on
  display — three visible inputs, two of them `type="tel"`, while `扫码登录` is
  the active tab. An early draft judged that as `sms_required` and reported it
  on **every poll of a completely normal scan**. The code field only counts once
  the QR panel is gone; the platform swaps the login card for the verification
  step rather than showing both. (The reference implementation dodges the same
  trap from a different angle, by only looking after the URL has changed.)
  `input[type="tel"]` is not a selector we use at all — Douyin gives it to both
  the phone number and the code field, so it distinguishes nothing.
- **QR selectors are a priority list, not a selector.** Both
  `div#animate_qrcode_container img[src^="data:image"]` and the older
  `img[aria-label="二维码"]` are currently present, but the reference project's
  notes record the `aria-label` hook disappearing once already. A single
  selector turns each redesign into a hard outage, so historical hooks stay as
  fallbacks rather than being deleted.
- **`手机号登录` is no longer rendered** on the current login page; `扫码登录`
  is. Both remain in the marker list for the same reason.
- **The refresh affordance is the QR container**, not the `二维码失效` caption —
  that caption is a text node with no click handler of its own.

`judge_douyin_login` decides what the user is told to do next, so it carries a
table test covering each state and, explicitly, the two orderings that matter:
success is checked before SMS (so one false-positive selector cannot wedge a
completed login), and SMS is checked before "expired" (a consumed code shows the
expired caption, and the expired branch *clicks refresh* — which would tear down
a 2FA flow the user is halfway through).

### Bounds

Every loop is bounded; there is no `while True` anywhere (design doc §7.2),
including the Xvfb socket wait in `entrypoint.sh` — **and a test asserts it**
structurally over `app/`, because that rule gets re-broken by the next person who
adds a poll. A validation call is capped per-attempt *and* in total wall clock;
admission to either browser pool is bounded; the QR read is a fixed number of
attempts; the per-session lock wait is bounded. A saturated service fails fast
instead of queueing forever.

## How publishing works

Same split again. `app/publish.py` owns the platform-neutral orchestration,
`app/assets.py` the media staging, and `app/platforms/douyin_publish.py` the DOM
work plus a set of **pure** judgements (`judge_editor_arrival`,
`judge_upload_state`, `judge_publish_outcome`) that decide what state the page is
in without touching Playwright.

The registry stores a **coroutine**, not a list of DOM steps. Design doc §6.1c
makes that a hard requirement: the second platform is expected to publish
*without* the DOM at all — the browser signs the request and the upload goes
over plain HTTP. An abstraction that assumed "publish == a sequence of clicks"
would have to be rebuilt to accept it.

The four stages run in this order because each is a filter that makes the next
one cheaper, and the tests assert it by giving the later stages tripwires:

1. **Check the intent** — pure, no I/O. A typo must not cost a browser launch.
2. **Check the session**, by calling the platform's *one* registered validator
   (§7.1), never a publish-flavoured second copy. Uploading a few hundred
   megabytes and only then finding nobody is logged in is the most expensive
   failure available here.
3. **Stage the assets** — after the session check, so a dead account costs no
   bandwidth. Downloaded into a temp directory; the **directory** is removed in
   a `finally`, so an interrupted partial download leaves nothing behind either.
4. **Hand off to the publisher.**

Every stage draws from one shared `Deadline` rather than owning an independent
timeout. Bounded stages that each restart the clock add up to an unbounded
whole, which is the failure §7.2 is really about.

### DOM notes (from the reference project's 2026-06 field record, §7.4)

Each is marked at its point of use in `douyin_publish.py`, not just here — a note
in a header is a note nobody reads while deleting the line it explains.

- **The cover dialog has four hidden file inputs, and `.first` is the wrong
  one.** `[0]`/`[1]` belong to the "AI reference image" panel; `[2]`/`[3]` are
  the real cover upload. Using `.first` uploads successfully, reports success,
  and produces a post with **no cover on it** — visible only on the published
  feed. The code takes `.nth(1)` and *refuses to publish* if the dialog exposes
  fewer inputs than expected, rather than falling back to a guess.
- **Two publish-page URLs run in parallel gray releases** and the account does
  not get to pick, so both are polled. Watching only one hangs about half the
  time — and it hangs after the upload is already paid for.
- **Onboarding coach-marks (`shepherd`) and the topic dropdown intercept
  clicks.** They are *removed* before each attempt, not clicked through: a
  forced click still lands on whatever is underneath, which on this page is
  another control. Re-stripped every pass, because they are re-injected on
  re-render.
- **Semi renders a radio's label as `.semi-radio-addon`, often with
  `pointer-events: none`.** Clicking it does not fail fast — it waits out the
  full actionability timeout and *then* fails, which reads like a hung page. The
  interactive element is the `.semi-radio` wrapper.
- **Some controls are `visibility: hidden` yet functional** (the reference's
  "use this BGM" button). `dom.click_element` escalates plain → `force` → DOM
  `click()` via JS, in that order; the JS step is last because it skips every
  actionability guarantee.
- **Upload failures self-heal**: the failed card carries its own replacement
  input, re-fed a bounded number of times.
- **The editor form only renders once the video has finished transferring**
  (~40s measured), so the form wait is minutes, not the conventional 30s — that
  ceiling fails on every real video.

One deliberate deviation from the reference: when the "upload failed" and
"replace video" markers are *both* visible, this code judges **failed**. The
mistakes are not symmetric — reading a failed upload as complete publishes a
broken post a human then has to find and delete, while the other way costs one
bounded re-upload.

### Options fail closed

`visibility` and `allow_download` are applied only when they **differ from the
platform default** (`public`, downloads allowed). If a non-default value has no
control on the page, the publish is **refused** rather than completed with the
default.

The asymmetry is the point: a post the user marked `private` going out publicly
cannot be taken back, while a refusal leaves a draft that costs an inspection.
Requests that already match the default are satisfied by touching nothing, so
this only ever bites when the answer matters.

⚠️ **These two controls are the least verified part of the file.** The reference
project implements neither, so their selectors are inference from Semi Design's
markup rather than observation — which is precisely why the missing-control path
refuses instead of guessing.

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
| `BROWSER_LOGIN_TTL_S` | `300` | Hard lifetime of a login session |
| `BROWSER_LOGIN_MAX_SESSIONS` | `3` | Concurrent live logins (separate pool from `BROWSER_MAX_CONCURRENT`) |
| `BROWSER_LOGIN_START_TIMEOUT_S` | `90` | Launch + navigate + first paint budget |
| `BROWSER_LOGIN_LOCK_WAIT_S` | `20` | Max queue time behind another operation on the same session |
| `BROWSER_LOGIN_QRCODE_ATTEMPTS` | `15` | QR read attempts (the login card is injected by JS after load) |
| `BROWSER_LOGIN_QRCODE_POLL_S` | `1.0` | Interval between those attempts |
| `BROWSER_LOGIN_CLICK_TIMEOUT_MS` | `10000` | Per click/fill ceiling |
| `BROWSER_LOGIN_SMS_SETTLE_S` | `3.0` | Pause after submitting a code before sampling |
| `BROWSER_LOGIN_REAPER_INTERVAL_S` | `15` | How often expired sessions are swept |
| `BROWSER_LOGIN_TERMINAL_GRACE_S` | `120` | How long a released session stays queryable as a tombstone |
| `BROWSER_LOGIN_STATE_GRACE_S` | `60` | One-shot extension granted on success, to collect `storage_state` |
| `BROWSER_PUBLISH_TOTAL_TIMEOUT_S` | `1200` | Whole-publish budget; every stage below draws from it |
| `BROWSER_PUBLISH_EDITOR_WAIT_S` | `180` | Wait for the upload page to hand over to the post editor |
| `BROWSER_PUBLISH_UPLOAD_WAIT_S` | `900` | Wait for the video bytes to finish transferring |
| `BROWSER_PUBLISH_UPLOAD_RETRIES` | `2` | Re-uploads after the page reports a failure |
| `BROWSER_PUBLISH_POLL_INTERVAL_S` | `2.0` | Page-state sampling interval in the upload / publish loops |
| `BROWSER_PUBLISH_FORM_TIMEOUT_MS` | `120000` | Wait for a form field to render (the editor renders only after the upload — a 30s ceiling fails on every real video) |
| `BROWSER_PUBLISH_CLICK_TIMEOUT_MS` | `10000` | Per click/fill ceiling on the editor |
| `BROWSER_PUBLISH_SETTLE_MS` | `1500` | Settle after an action whose effect is asynchronous |
| `BROWSER_PUBLISH_CONFIRM_ATTEMPTS` | `20` | Publish-button attempts, each one self-healing |
| `BROWSER_PUBLISH_CONFIRM_WAIT_S` | `5` | Per-attempt wait for the post-publish redirect |
| `BROWSER_ASSET_DOWNLOAD_TIMEOUT_S` | `600` | Whole-file download budget per asset |
| `BROWSER_ASSET_MAX_BYTES` | `2147483648` | Refuse anything larger, enforced against bytes actually received |
| `BROWSER_ASSET_CHUNK_BYTES` | `1048576` | Streaming chunk size (assets are never held in memory whole) |

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

The image deliberately ships neither `tests/` (excluded in `.dockerignore`) nor
the dev extras (`uv sync --no-install-project` installs runtime deps only) — a
production image has no business carrying a test runner. So inject both into a
running container rather than baking them in:

```bash
docker build -t nous-browser:local .
docker run -d --name nous-browser-test --shm-size=1g --init \
  -e BROWSER_INTERNAL_TOKEN=dev-token nous-browser:local

# entrypoint.sh already started Xvfb on :99 and left it running, so the
# integration tests inherit a working display - no need to start one here.
docker cp tests nous-browser-test:/app/tests
docker exec nous-browser-test uv pip install --python /app/.venv/bin/python \
  pytest pytest-asyncio httpx
docker exec nous-browser-test python -m pytest -q          # whole suite, nothing skipped

docker rm -f nous-browser-test
```

The login integration tests reach `creator.douyin.com` for real. They assert the
QR selectors still match and that an abandoned session's **browser process**
(not merely its registry entry) is gone after a sweep. They `skip` rather than
fail when the network cannot reach the platform, so a restricted machine does
not produce a false red — check for skips before reading a green run as
"selectors verified".

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
- **No publish has ever reached a live account from here.** `/session/publish`
  is exercised by unit tests against fakes, and its selectors come from the
  reference project's field record rather than from a run against
  `creator.douyin.com` — there is no account in this environment to run one
  with. The **first real publish is the acceptance test**, and the two places to
  watch are the cover dialog's input index and the visibility / download
  controls (§"Options fail closed"). Everything else in the flow at least has a
  documented sighting behind it.
- **Publishing is video-only.** Image posts (图文), scheduling and BGM are
  separate increments; `scheduled_at` is refused rather than approximated.
- **No account-level serialisation yet** (design doc §7.5). Two concurrent
  sessions for one account can knock each other offline; the lock belongs on the
  backend side, which is the component that knows about accounts.
- **The publish hard timeout forfeits the session renewal.** `run_publish` is
  cooperative — it checks its deadline between steps and returns, so the
  refreshed cookies still come back. The outer ceiling in `main.py` exists only
  for a single Playwright call wedging below that granularity, and firing it
  loses the renewal. It sits well above the real budget for that reason, and
  says so via `detail.storage_state_forfeited`.
- **`scanned` has not been observed against the live platform.** Reaching it
  needs a real phone scanning a real code, which no automated test here can do.
  The marker texts (`扫码成功` / `请在手机上确认` / …) are educated guesses; if
  they are wrong the flow still works — it simply reports `waiting_scan` through
  the confirm step instead of `scanned`, which is a cosmetic UI regression, not
  a broken login. Same caveat, with more at stake, for `sms_required` and the
  refresh click: both are exercised only by unit tests, because provoking them
  requires an account and an expired code.
- **Login sessions are in-process state.** A restart drops every in-flight scan
  (the reaper closes them cleanly on shutdown). That is correct for a 5-minute
  interactive flow, but it does mean login cannot be load-balanced across
  replicas without sticky routing on `login_session_id`.
