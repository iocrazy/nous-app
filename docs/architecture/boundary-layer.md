# Boundary Layer — MediaHub Trust Boundary Contract

**Status:** Active (Sprint 1 v2)
**Owner:** heygo
**Last updated:** 2026-05-01

## What this is

`app/boundary/` is MediaHub's explicit trust boundary. It is the only place that knows how to safely accept untrusted external data and convert it into something the business layer is allowed to use.

Everything outside `app/boundary/` is the **trusted interior**. The interior is allowed to assume that any data it receives has already been validated. Any module that imports from outside the worktree (network, user input, third-party API response, file content) must route the data through a `app/boundary/<X>` validator first.

## Why this exists

Before Sprint 1, MediaHub had no architectural answer to "where does input validation live?". SSRF protection, prompt-injection sanitization, log redaction, and timing-safe secret comparison were either missing or scattered into ad-hoc per-service helpers. Sprint 1 makes the trust boundary a first-class layer.

## The contract

Every boundary module exports one or more validators with this shape:

```python
def validate_<thing>(raw: <PrimitiveType>, *, <policy_kwargs>) -> <ValidatedType>:
    """Raise BoundaryError subclass if input is unsafe / unwanted.
    Otherwise return a wrapped value that downstream code accepts."""
```

Validators must:
1. **Fail closed.** Unknown input → reject. Never default-allow.
2. **Be pure.** No I/O except DNS lookup (for SSRF). No DB writes, no logging of the raw input. Errors surface to caller.
3. **Return a wrapped type** when the validation result needs to flow through multiple call sites. The wrapper signals "this has been validated" to type checkers AND a runtime `assert isinstance(x, ValidatedX)` at every retro-fitted entrypoint provides an immediate guard while mypy/pyright are not yet wired into CI (deferred to Sprint 2).
4. **Be cheap.** ≤10ms typical; SSRF DNS lookup is the only allowed slow path, must run via `loop.getaddrinfo()` with a 2-second timeout, never `socket.getaddrinfo()` on the FastAPI event loop.
5. **Be 100% covered by unit tests** with adversarial inputs.

Service interiors must:
1. Accept the wrapped type, not the primitive. `def fetch_metadata(url: ValidatedURL)` not `def fetch_metadata(url: str)`.
2. Add `assert isinstance(url, ValidatedURL)` as the first line of the function body — the runtime guard that compensates for missing mypy in CI.
3. Never re-validate. Trust the type.
4. Never bypass the boundary by calling `httpx.get(some_user_string)` directly. Lint will catch this in Sprint 2.

## Trusted server-config carve-out

Server-side configuration values read from `settings.*` (Pydantic Settings, .env, secrets manager) are **NOT** subject to boundary validation. They are part of the trusted interior because:

- An attacker who can modify `settings.SUPABASE_URL` already has full control of the deployment
- The Supabase URL deliberately points to internal infrastructure (192.168.50.9:9080 on the NAS in production, 127.0.0.1:9081 in dev)
- Forcing every `settings.*` URL through `validate_url` would make the system unbootable

This carve-out is intentional. It does NOT extend to:
- URLs received from the database (`agent_config.fallback_url`, `external_config.webhook`) → those still pass through `validate_url` because the DB row may have been written based on user input
- URLs from request bodies, query strings, headers, or file uploads → always validated
- URLs constructed by string-concatenating user input with a settings prefix (e.g., `f"{settings.NOTION_BASE}/{user_path}"`) → validate the **constructed result**, not the prefix

When in doubt: if the value's source is the deployment operator (env var, config file under git), it is trusted. If the source is anyone the operator does not know personally, it is not.

## Sprint 1 modules

| Module | Validator | Purpose |
|---|---|---|
| `url_guard` | `validate_url(raw) -> ValidatedURL` (sync) + `validate_url_async(raw) -> ValidatedURL` (async) | Reject SSRF: RFC1918 + loopback + link-local + IPv6 ULA + decimal/octal/hex IPv4 literals + `.localhost`/`.local`/`.internal` suffixes + GCP/Azure IMDS hostnames + MediaHub NAS subnet (configurable) + non-http(s) schemes. DNS path uses async `loop.getaddrinfo()` with 2s timeout and 60s LRU cache. |
| `external_text` | `neutralize_external_text(raw, *, max_chars) -> str` | Defang prompt-injection patterns in yt-dlp `description` and external captions before they enter LLM prompts. Wraps content in `<EXTERNAL_CONTENT_<random>>` markers (random suffix per call to prevent forgery) and brackets known instruction-override literals. |
| `log_redact` | `redact(value) -> value` + `make_loguru_patcher() -> Callable` | Mask `sk-…`, `Bearer …`, JWT-shaped tokens, and `KEY=value` env-style secrets at log write time. Patcher walks both `record["message"]` and `record["extra"]` dict so `logger.bind(token=…).info(…)` is also redacted. Installed in `app/core/utils.py` next to existing loguru config. |
| `secret_compare` | `compare_secret(actual: str, candidate: str) -> bool` | Timing-safe secret comparison via `hmac.compare_digest`. Use everywhere a secret-shaped string is compared with `==` (API keys, webhook signatures, JWT HMAC). |

## SSRF dev allowlist

Setting `SSRF_DEV_ALLOWLIST` (CIDR list, comma-separated) explicitly allows additional networks past the SSRF block. **Empty in production.** Dev example:

```bash
# .env (dev only, never in prod)
SSRF_DEV_ALLOWLIST=192.168.50.10/32,127.0.0.1/32,192.168.50.20/32
```

This unblocks "mediahub locally calls another service on the same LAN" workflows while keeping the default-deny posture. Each entry is verified at startup and logged so the operator sees what holes are open.

## Errors

`BoundaryError(Exception)` is the base class. Subclasses:
- `URLBlockedError(BoundaryError)` — SSRF / scheme reject. HTTP layer maps to 400 with safe message (does NOT echo the raw URL).
- `ExternalTextRejectedError(BoundaryError)` — text size or content reject. Caller decides recovery.
- `SecretMismatchError(BoundaryError)` — secret comparison failed. Caller responsible for not leaking which side mismatched.

A global `@app.exception_handler(BoundaryError)` in `app/core/utils.py` converts any uncaught BoundaryError to HTTP 400 with `detail="invalid input"`, logging the full reason server-side via `logger.warning(redact(...))`. This protects against future endpoints forgetting per-handler try/catch.

## Known limits

These are explicit gaps the layer does NOT defend against in Sprint 1. They are documented to set correct expectations, not because they are unimportant.

1. **DNS pinning / TOCTOU.** `validate_url` resolves DNS once, but the downstream HTTP client (yt-dlp, httpx) re-resolves at fetch time. A determined attacker controlling DNS could return public IPs to the validator and private IPs to the fetcher (DNS rebinding window typically <1 second). Sprint 1 mitigates the easy case (cached DNS within 60s window). Full pinning (resolve once, connect to that exact IP) is Sprint 2.
2. **Response body size.** No `max_bytes` cap on responses. A malicious URL returning 100GB will consume disk before the timeout fires. Sprint 2.
3. **`path_guard`.** Upload symlink defense, realpath enforcement. Sprint 2.
4. **`user_regex` (ReDoS).** No defense against user-supplied regex causing catastrophic backtracking. No current attack surface (users do not write regex), Sprint 2 if added.
5. **Cloudflare / Vercel WAF defense-in-depth.** Edge-layer SSRF rejection (deny RFC1918 in request body before reaching FastAPI) is a Sprint 2 candidate.

## Redirect handling — wrapped

yt-dlp follows HTTP redirects internally by default. A public URL can 302-redirect to `192.168.50.9:9080` and the boundary never sees the redirect. Sprint 1 v2 wraps yt-dlp's HTTP client (downloader_options hook) so each redirect hop re-runs `validate_url` and strips `Authorization` / `Cookie` headers on cross-origin redirect (per OpenClaw `redirect-headers.ts`). This is the only way to make the boundary defense complete for the media-fetch path.

## What is NOT in the boundary

- **Authn/authz** — `app/api/deps.py`, separate concern.
- **DB constraint validation** — pydantic schemas in `app/schemas/`.
- **Business rule validation** — service layer (e.g., "this user can't transcribe more than 100 videos / day").
- **LLM provider URLs** (`ai_provider.py`, `storyboard_ai_service.py`, etc.) — these are admin-configured base URLs (Doubao, Qwen, OpenAI). They fall under "trusted server-config carve-out" above.

The boundary is only about untrusted external data crossing into the system. Once inside, everything else is business.

## Alternatives considered

Three approaches were evaluated before settling on "build the layer in Python":

**Option A: Port OpenClaw modules wholesale.** OpenClaw's `infra/net/ssrf.ts` (~556 lines) and `security/external-content.ts` (~426 lines) are TypeScript and battle-tested. Direct line-by-line port to Python: ~2 days. **Decision:** port the **threat model** and **test corpus** even though we re-implement in Python. Specific borrowed elements: decimal/octal/hex IPv4 detection, IMDS hostname blocklist, random per-message marker IDs, `record["extra"]` walking, redirect-safe header strip.

**Option B: Existing Python libraries.** Surveyed: `defusedxml` (XXE only, irrelevant); `python-ssrf-protect` (last commit 2021, unmaintained); `safeurl-python` (small, decent but only IP block, no marker / redaction). **Decision:** Python ecosystem is weak for this; build in-house with explicit OpenClaw alignment is the right call.

**Option C: Cloudflare WAF / Vercel Edge SSRF.** A WAF rule "reject request body containing private IPs in `url` field" is ~30 lines and protects every endpoint. **Decision:** add as Sprint 2 defense-in-depth, do **not** replace `url_guard`. WAF cannot see DNS rebinding or post-redirect destinations; in-app boundary is the must-have layer.

## Enforcement

**Sprint 1 v2** (this sprint):
- Type-checker hint via `ValidatedURL` wrapper subclass of `str`
- Runtime `assert isinstance(url, ValidatedURL)` in every retro-fitted method's first line — the load-bearing guard, not the type checker
- Global `BoundaryError` exception handler ensures any `BoundaryError` becomes 400 (not 500)

**Sprint 2** (deferred):
- mypy / pyright in CI to catch raw `str` at static-analysis time
- `ruff` rule `RUF-BOUNDARY` banning `httpx.get(` / `requests.get(` outside `app/boundary/` and `app/services/ai_provider*`

## Audit log

**2026-05-02 — Sprint 1 v2 secret compare audit (UC2 / B10).** Grepped the
codebase for `==` / `verify` / `compare_hash` patterns on secret-shaped
variables (token, api_key, signature, hmac, password, webhook, secret).

Findings:
- `app/api/media_auth.py:86` — already uses `hmac.compare_digest(sig, expected_sig)` ✅
- `app/api/temp_token_router.py` — Redis lookup pattern (`await redis.get(prefix:token)`); no string compare exists
- `app/api/api_key_router.py:142,146` — Redis lookup + ownership check; no secret compare

**Zero retro-fit sites.** `app/boundary/secret_compare.py` ships as preventive
infrastructure — any future secret compare must use `compare_secret` or
`require_secret`, not raw `==`.

## Reference

- OpenClaw `infra/net/ssrf.ts` — IPv4 decimal/octal detection, IMDS hostnames
- OpenClaw `infra/net/fetch-guard.ts` — redirect re-validation pattern
- OpenClaw `infra/net/redirect-headers.ts` — cross-origin Authorization strip
- OpenClaw `security/external-content.ts` — random marker IDs, full instruction-literal list
- OpenClaw `security/secret-equal.ts` — timing-safe + padded compare
- OpenClaw `logging/redact.ts` — sensitive-token regex
