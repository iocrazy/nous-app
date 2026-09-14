# OpenAI Images (GPT Image 2.5) provider for the canvas — design

**Date:** 2026-09-13 · **Status:** approved direction (user: 「继续」 on the 09-09 assessment)

## 1. Problem

OpenAI shipped GPT Image 2.5 on 2026-09-08 as two API models, `gpt-image-2.5-flare`
(fast, default) and `gpt-image-2.5-sunburst` (edit precision, slower). New knobs:
quality tiers `xhigh` / `max`; exact `WIDTHxHEIGHT` sizes (multiples of 16, aspect
1:3–3:1, no edge over 3840 px, 655,360–8,294,400 pixels); `background: transparent`;
`--mask` / `--input-fidelity` on edits; up to 16 reference images.

The canvas today reaches OpenAI images only through the **Codex subscription path**
(`gpt-image-2-skill --provider codex` → `chatgpt.com/backend-api/codex/responses`).
Measured 2026-09-09 on production:

| requested | what the subscription backend did |
|---|---|
| `tools[].model = gpt-image-2.5-flare`, `quality = xhigh` | accepted, `quality` echoed back as `medium` |
| any `size` | ignored, always 1254×1254 |
| which image model ran | never reported |

So on the subscription path the image model is OpenAI's rollout decision and the
new knobs are not honoured. Nothing in our code can change that. **To use 2.5's
capabilities the canvas needs an API-key path.**

`gpt-image-2-skill` 0.7.4 (released 2026-09-13) adds `xhigh` / `max` to its
`--quality` enum and, on `--provider openai`, `--model` is the image model
(`gpt-image-2.5-flare` / `-sunburst`). It reads the key from `OPENAI_API_KEY`
(`doctor` reports `auth_source: env`). Edits gained `--mask` and
`--input-fidelity`. The codex provider still has no image-model selector.

## 2. Goals / non-goals

**Goals**

1. A new image protocol `openai-images` that drives the same binary with
   `--provider openai`, key from the catalog row, `--model` = the 2.5 model id.
2. Quality tiers become a vocabulary the capability contract carries
   (`quality_tiers`), so `xhigh` / `max` appear only on models that honour them
   and are dropped loudly elsewhere.
3. Resolution 1K / 2K / 4K becomes a real pixel size on this path
   (`(ratio, resolution) → WxH`, validated against OpenAI's constraints).
4. CLI pinned to 0.7.4 in the backend image.
5. Codex catalog rows stop naming a version they do not control.

**Non-goals (deferred, recorded in §8)**

- Transparent background, `--mask` inpaint, `--input-fidelity`, 16 references
  (the canvas keeps its global 9-reference ceiling).
- Multi-turn editing (`previous_response_id`) — 2.5 is not on the Responses API.
- A per-user OpenAI card on the Settings providers page for images (see §3.4).
- Streaming partial images.

## 3. Design

### 3.1 Protocol `openai-images`

`backend/app/services/ai/provider_protocols/openai_images.py`:

- `key = "openai-images"`, `label = "OpenAI Images API (GPT Image 2.5)"`,
  `model_types = ("image",)`, `generation_family = "openai-images"`,
  `supports_http_image_probe = False` (shells out to the CLI, same as `codex`).
- `capabilities = ProviderCapabilities(ratios=ALL_RATIOS, quality=True,
  quality_tiers=IMAGE_25_QUALITY_TIERS, resolution=True, max_refs=9,
  negative=False, video_modes=frozenset(), honours_ratio="native")`.
- `build_image_provider(row)` → `_CodexImageAdapter(CodexCliProvider(
  provider_kind="openai", api_key=row["api_key"])), row["actual_model"]`.
  Empty `api_key` raises `ProtocolCapabilityError`-style typed error at build
  time (`openai_images_key_missing`) so dispatch answers with a typed refusal,
  never a CLI `not_logged_in` masquerading as auth.

The adapter is the existing `_CodexImageAdapter`; it gains one line — forward
`resolution=kwargs.get("resolution")`.

### 3.2 `CodexCliProvider` parametrisation (`codex_cli.py`)

- `__init__(..., provider_kind: Literal["codex", "openai"] = "codex",
  api_key: str | None = None)`.
- `_run_cli`: global flags become `["--json", "--json-events", "--provider",
  provider_kind]`; `--auth-file` only for `codex`; for `openai` the key travels as
  `safe_popen_kwargs(env_extra={"OPENAI_API_KEY": key})` — **never on argv**
  (process table). The existing scrub still strips every other `*KEY*` from the
  child environment.
- `generate_image(..., resolution: str | None = None)`:
  - `codex` kind: unchanged — `CODEX_SIZES[ratio]`, prose aspect hint appended,
    refs ≤ 9, `--background opaque`.
  - `openai` kind: `--size image_size_for(ratio, resolution)`, **no** prose hint
    (the API honours `--size`; the hint would only bias the picture), refs ≤ 9,
    `--background opaque` (canvas frames are full frames; transparent is §8).
- `health()`: for `openai` kind reads `providers.openai.auth.api_key_present`.

Everything else (timeout, kill, `_classify_error`, `_measure`, result parsing)
is shared unchanged.

### 3.3 Capability vocabulary

`ProviderCapabilities` gains `quality_tiers: frozenset[str]` with module
constants in `base.py`:

```python
LEGACY_QUALITY_TIERS = frozenset({"low", "medium", "high"})
IMAGE_25_QUALITY_TIERS = LEGACY_QUALITY_TIERS | {"xhigh", "max"}
QUALITY_TIER_ORDER = ("low", "medium", "high", "xhigh", "max")
```

Every existing protocol with `quality=True` declares `LEGACY_QUALITY_TIERS`;
`_NONE` declares `frozenset()`. `GenerationRequest.reconcile` drops a `quality`
that is not in `caps.quality_tiers` (appends `"quality"` to `dropped`, same
entry as today — one vocabulary for "not sent"). The capabilities endpoint
projects `quality_tiers` in `QUALITY_TIER_ORDER`; `honours_ratio` and
`actual_provider` stay withheld.

Frontend `ModelCapabilities.quality_tiers: string[]`; `QUALITIES` in
`GenFooterControls` lists Auto + the five tiers and filters by
`caps.quality_tiers` (`caps === null` keeps today's "unknown = everything").
Labels: `Low / Medium / High / Extra High / Max`.

### 3.4 Where the API key lives

Image providers already take their credential from the admin catalog row
(`mediahub_models.api_key`, encrypted on write by the admin router — this is
how `ark` works). `openai-images` follows that: the operator pastes the key into
each 2.5 row in Admin → AI Models. The user-facing Settings providers page keeps
its OpenAI card for **chat**; wiring images into it would create a second,
divergent credential source for the same vendor. Recorded as a deliberate
choice; revisit if per-user image billing ever becomes a product need.

### 3.5 Catalog rows (migration 465)

Two `image` rows, `actual_provider='openai-images'`, `api_key=''`,
`is_enabled=FALSE` (enabled by the operator once the key is in):

| name | display_name | actual_model | sort_order |
|---|---|---|---|
| `openai-image-flare` | `GPT Image 2.5 Flare (OpenAI API)` | `gpt-image-2.5-flare` | 21 |
| `openai-image-sunburst` | `GPT Image 2.5 Sunburst (OpenAI API)` | `gpt-image-2.5-sunburst` | 22 |

The `(OpenAI API)` suffix is the billing signal in the picker: this path is
per-token pay-as-you-go ($30 / 1M image-output tokens), unlike the subscription
rows. Same migration renames the codex rows to drop the version they do not
control: `codex-image` / `codex-local-image` → `GPT Image (Codex)` /
`GPT Image (Codex, local)` (UPDATE by name; the local row exists only in
production, created through admin — the UPDATE is a no-op where absent).

Price coverage: `PRICED_MODEL_TYPES` is `{"llm"}`, image rows report
`not_applicable`; no `ai_model_prices` entry is needed (mig 454's red tag does
not fire).

### 3.6 Size vocabulary (`aspect.py`)

`IMAGE_SIZES: dict[tuple[str, str], str]` keyed `(ratio, resolution)` for the
eight ratios × `1k / 2k / 4k`, plus `image_size_for(ratio, resolution) -> str`
(unknown → `"1024x1024"`). A test validates every entry against OpenAI's rules:
both edges multiples of 16, edge ≤ 3840, pixel count within
[655,360, 8,294,400], aspect within `ASPECT_TOLERANCE` of the ratio, and 4K
entries at or above 2K entries in pixels. Values are chosen so 4K sits at the
ceiling (16:9 → 3840×2160 = 8,294,400 exactly; 1:1 → 2880×2880).

### 3.7 CLI pin

`Dockerfile`: `GPT_IMAGE_2_SKILL_VERSION=0.7.4`,
`GPT_IMAGE_2_SKILL_SHA256=0488a72fa009cf69a1c0e57f9997de288da13f52673242826cae5420daffb575`
(sha256 of `gpt-image-2-skill-linux-x64-static-0.7.4.tgz`, computed 2026-09-13).
The daemon path (`tools/codex-daemon`) is untouched; its README's install line
notes 0.7.4 as the tested version.

## 4. Acceptance (real stack, after the operator enters the key)

1. Admin → AI Models shows `openai-images` in the protocol dropdown; the two rows
   exist disabled; enabling one makes it appear in the canvas picker with the
   `(OpenAI API)` suffix.
2. Generate with `GPT Image 2.5 Flare (OpenAI API)`, ratio 16:9, resolution 2K,
   quality Extra High → a PNG whose measured size is 2560×1440;
   `dropped_knobs` empty; `api_request_logs` shows one generation dispatch.
3. Same on a codex row with quality Max → `dropped_knobs` contains `quality` and
   the UI never offered Max for that row (capabilities endpoint lists only
   `low/medium/high`).
4. `docker exec nous-worker gpt-image-2-skill -V` prints 0.7.4; `doctor` under
   `--provider openai` with the row's key reports `api_key_present: true`.
5. `ps` on the worker during a generation shows no `sk-` string on any argv.
6. Existing codex subscription rows generate exactly as before (shape hint
   still appended, `--background opaque` intact).

## 5. Risks

- **Argv leak** — mitigated by env passthrough (§3.2) and a source-guard test
  that `--api-key` never appears in `codex_cli.py`.
- **Sizes rejected by OpenAI** — the constraint test (§3.6) encodes the
  published rules; a rule change surfaces as a typed `generation_failed` with
  the API's message, not a silent fallback.
- **Cost** — `max` at 4K is materially more expensive than the subscription
  path; the display-name suffix is the only in-product signal in this phase.

## 6. Out of scope, recorded (§8 of the running list)

Transparent background (needs the green-matte story re-measured on the API
path), `--mask` inpaint (canvas mask is a second reference today, not an
inpaint call), `--input-fidelity`, 16 refs (global ceiling 9), streaming,
`previous_response_id`, a per-user images card on Settings.
