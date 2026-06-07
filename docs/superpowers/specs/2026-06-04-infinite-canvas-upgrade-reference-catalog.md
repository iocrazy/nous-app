# Infinite Canvas Upgrade — Reference Catalog & Adoption Plan

> **Goal:** comprehensively catalog the open-source `Infinite-Canvas` tool, map every
> feature/detail against MediaHub's current Storyboard Workbench, and form a phased
> adoption plan. Target: **100k users, commercial product**.
>
> **License guardrail:** the reference repo is **non-commercial / no repackaging**
> → we take **ideas & architecture only, never port its code**. Everything below is
> re-implemented cleanly in MediaHub's stack (React 19 + @xyflow + FastAPI + DBOS +
> Supabase), billed through the existing **Nous platform-model + credits** system.
>
> **Status legend:** ✅ MediaHub already has · 🔶 partial · ❌ gap (candidate to introduce)
>
> _Draft for review — not yet committed. Sources: 3 exploration passes over the
> reference repo (docs / static frontend / main.py backend)._

---

## Part 0 — The two products side by side

| | Reference `Infinite-Canvas` | MediaHub Storyboard Workbench |
|---|---|---|
| Stack | Python monolith (`main.py` 509KB) + vanilla JS frontend | React 19 + @xyflow + FastAPI + DBOS + Supabase |
| Canvas modes | **TWO**: 普通无限画布 (node editor) + **智能画布 Smart Canvas** (card-based beginner mode) | ONE (node editor only) |
| Providers | OpenAI, APIMart(async), Gemini, Volcano/方舟, ModelScope(free+LoRA), RunningHub, **ComfyUI(local/LAN)**, **即梦 CLI** | OpenAI, FAL, GRSAI, KIE (4 cloud) |
| Consistency | reference-image + prompt-template presets + ComfyUI workflows (IPAdapter/ControlNet/LoRA) | character text-prepend only |
| Asset library | **global cross-canvas Material Library** (@-ref, tagging) | per-project characters table only |
| Themes | **black/white (dark/light)** + UI scale 100/115/125/140 | (verify current) |
| Persistence | JSON files | Postgres (proper) ✅ better |
| Billing | none (single-user) | **Nous credits** ✅ better |
| Scale | single machine | designed for 100k ✅ |

**Takeaway:** MediaHub has the better *foundation* (DB, billing, scale, DBOS). The
reference has the better *creative surface* (more providers, Smart Canvas, global
asset library, image editor, consistency presets, theming, UX polish). The plan =
graft the reference's creative surface onto MediaHub's foundation.

---

## Part 1 — Capability catalog (what to introduce)

### A. Canvas modes & workspace

| Feature | Status | Introduce? |
|---|---|---|
| Node-editor infinite canvas (pan/zoom/group/connect/undo) | ✅ | — |
| **Smart Canvas** — card-based "drag image → pick engine → prompt → run" beginner mode | ❌ | **P1** — huge for 100k mainstream users (node editor scares non-pros) |
| **Global preview hotkey `Z`** (whole-graph overview) | ❌ | P3 cheap polish |
| Minimap / 导航地图 | 🔶 verify | P3 |
| Multi-tab / multi-canvas open at once | 🔶 | P3 |
| Canvas gate / gallery (cards w/ color markers ×9, pin star, emoji icon, owner chip) | 🔶 | P2 (project browser polish) |
| Knife/cut mode (slice edges) | ❌ | P4 |

### B. Node types (reference → MediaHub)

| Reference node | MediaHub equiv | Introduce? |
|---|---|---|
| Image (upload/paste/drag) | ✅ UploadNode | — |
| Prompt node (+ prompt library) | 🔶 inline | **P2** — dedicated prompt node + reusable prompt library |
| Generator (API t2i/t2v) | ✅ ImageEdit/StoryboardGen | — |
| **LLM node** (chat / image-understanding / **reverse-prompt**) | ❌ | **P2** — describe-image→prompt, vision captioning; you have AI runtime already |
| **Loop node (循环)** — base prompt + N variations, parallel batch | ❌ | **P1** — batch product shots / expression sheets / multi-scene |
| ComfyUI node | ❌ | P2 (see Providers) |
| RunningHub node | ❌ | P3 |
| ModelScope node (free + LoRA) | ❌ | P3 |
| Video node (i2v/t2v) | 🔶 backend exists, no UI | **P1** — wire existing `storyboard_video_workflow` to a canvas node |
| **LTX Director node** (timeline editor, 1000×800) | ❌ | P2/P3 — the "director" surface |
| Output node (collect/preview/download grid) | 🔶 ImageNode | P3 |
| Group node | ✅ | — |
| Text annotation | ✅ | — |

### C. Asset / Elements library — **the consistency foundation** 🔥

The reference's **global Material Library** (cross-canvas, tag-organized, `@`-referenced)
is exactly the "Elements/Cast" engine we want for 固定人物/场景/道具.

| Feature | Status | Introduce? |
|---|---|---|
| Per-project character table (`storyboard_characters`, visual_traits, ref image) | ✅ | — |
| **Global** asset library across all canvases/projects | ❌ | **P1** |
| Asset **types beyond characters**: scene, prop, style-reference, video-material | ❌ | **P1** (props/scenes = your ask) |
| Tag organization (character/scene/style/video) | ❌ | P1 |
| **`@`-reference** an asset into a prompt/shot | 🔶 @Image1 token only | **P1** — generalize to `@Character`/`@Scene`/`@Prop` |
| Hover-preview popup, rename, category mgmt | ❌ | P2 polish |
| Save canvas output → library | ❌ | P2 |

### D. Consistency mechanisms (the LTX-style locking you asked about)

Reference's actual approach (confirmed): **NOT** native IP-Adapter — it's
reference-image + **prompt-template presets** + **ComfyUI workflows** + upstream model.

| Mechanism | Status | Introduce? |
|---|---|---|
| Character as **text** prepend (weak) | ✅ | — (keep as floor) |
| Single **reference-image conditioning** per gen | 🔶 reference_image_url | P1 — generalize to multi-reference w/ roles |
| **Consistency prompt-template presets** (9-angle turnaround, 3-view face sheet→Actor-ID lock, full-body costume lock, 6-expression sheet, 360 panorama=scene lock, lighting compare) | ❌ | **P1** — cheap, high-value, provider-agnostic |
| **Reference roles**: `reference_image` / `first_frame` / `last_frame` / `mask` | 🔶 | P1 (first/last frame = video bookends) |
| **IP-Adapter / ControlNet / LoRA** via ComfyUI workflow | ❌ | **P2** — strong consistency path (self-host GPU / premium tier) |
| Character **LoRA training** | ❌ | P3 — strongest, costly; premium async tier |
| **Seed lock across a batch** | ❌ (per-node only) | P1 — lock seed+model across Loop/StoryboardGen frames |

> **Strategy (matches Nous + 100k):** floor = text+reference-image+presets (cheap, all
> cloud providers, scales). Premium = ComfyUI/LoRA (IP-Adapter/ControlNet/trained
> LoRA), async-queued, metered via Nous credits. Don't put GPU in the普惠 path.

### E. Provider integrations (graft onto Nous)

| Provider | What it adds | Introduce? |
|---|---|---|
| 4 cloud image (OpenAI/FAL/GRSAI/KIE) | ✅ have | — |
| **即梦 CLI (Dreamina)** — t2i/i2i/t2v/i2v incl. **frames2video / multiframe2video / multimodal2video**, membership credits | ❌ | **P2** — strong cheap video + character consistency; CLI subprocess model |
| **ComfyUI (local/LAN)** — workflow JSON inject, IPAdapter/ControlNet/LoRA, multi-instance load-balance + cross-instance image sync | ❌ | **P2** — the real consistency engine; self-host GPU = premium tier |
| ModelScope (free image + LLM + VL + **LoRA**) | ❌ | P3 — free tier sweetener |
| RunningHub (cloud workflows / AI apps) | ❌ | P3 |
| Volcano/方舟, Gemini | ❌ | P3 (Gemini for LLM/VL maybe sooner) |
| **Async task + polling model** (submit→202→poll, 1800s video timeout, exp backoff) | 🔶 DBOS already | map cleanly onto DBOS workflows ✅ |

### F. Image editor (double-click an image → modal)

| Tool | Status | Introduce? |
|---|---|---|
| Preview / zoom / pan / **side-by-side compare slider** | 🔶 | P2 |
| Crop | ❌ | P2 |
| **Mask + brush local edit** (AI edits only masked region) | ❌ | **P2** — inpaint, very useful |
| **Outpaint / 扩图** (extend beyond bounds) | ❌ | P2 |
| **Grid slicing 网格切图** (split 3×3 → tiles) | ❌ | **P2** — pairs w/ turnaround/expression sheets |
| **Video frame extraction 抽帧** (ffmpeg → key frames) | 🔶 backend video-analysis exists | P2 — expose as node/editor action |
| **360 panorama 全景** WebGL sphere preview | ❌ | P3 (scene asset preview) |

### G. Execution model

| Feature | Status | Introduce? |
|---|---|---|
| On-demand per-node generation (pull) | ✅ | — |
| **Run-the-graph DAG** (execute whole pipeline char→shot→video) | ❌ | P2 — DBOS-orchestrated graph run |
| Cascade run (chained, stop-on-error) | ❌ | P3 |
| Async task + status polling | ✅ DBOS | — |

### H. Realtime / collab

| Feature | Status | Introduce? |
|---|---|---|
| WebSocket multi-window live sync (same canvas) | ❌ (viewport poll only) | P3 — you have Supabase Realtime; real collab is a big sub-project |
| Generation broadcast to all clients | 🔶 task poll | P3 |

---

## Part 2 — UI/UX & theming catalog (the "很多细节")

### Theming — black/white (dark/light), the user's explicit ask

- **Class-based toggle + CSS variables.** `localStorage['studio_theme']`; toggles
  `studio-theme-dark`/`theme-dark` on `<html>`/`<body>`; cross-tab sync via storage event.
- **Two full token palettes** (`static/css/canvas.css`), 15 tokens each — adopt verbatim as our theme tokens:
  - **Light:** `--page:#f8fafc · --panel:rgba(255,255,255,.92) · --card:#fff · --line:#e8edf3 · --text:#111827 · --muted:#64748b · --strong:#111827 · --strong-text:#fff · --grid:#d9e1ea · --shadow:rgba(15,23,42,.08)`
  - **Dark:** `--page:#0b1020 · --panel:rgba(17,24,39,.9) · --card:#111827 · --line:#334155 · --text:#f8fafc · --muted:#cbd5e1 · --strong:#f8fafc · --strong-text:#0f172a · --grid:rgba(148,163,184,.16) · --shadow:rgba(0,0,0,.28)`
- **UI scale system** (`StudioScale`): auto / 100 / 115 / 125 / 140 — accessibility win.
- Custom scrollbars (thin, rounded pill, opacity hover), `scrollbar-gutter:stable`.

→ **Introduce:** P1, foundational. A `--token` theme layer + dark/light + scale, applied
to the whole canvas (and ideally the whole app). Map to Tailwind theme / CSS vars.

### Node card design (adopt the visual language)

- Min 220×96, radius 22 (head 42px, `cursor:move`), selection = 2px solid `--strong` +2 offset.
- Resize handle 18×18 bottom-right on hover. Ports 14×14 circles, hover scale 1.22 + shadow, hidden until node hover/select.
- Per-node sizes worth matching: Prompt 310 · Generator 380 · ComfyUI 420×460 · LLM 420 · Video 380 · RunningHub 430 · **LTX Director 1000×800** · Output 460.
- **Run status badges** (queued/running/done/failed = gray/blue/green/red) + `zapPulse` on running gen button + spinner + shimmer skeleton.

### Connections / edges

- SVG links, 2.5px stroke opacity .82; temp=dashed `dash` anim; **delete control** = 22px circle on hover (red); 18px transparent hit-area for easy targeting.

### Panels / chrome

- Fixed top nav (rounded 24, blur backdrop), collapsible right quick-toolbar (all node-add buttons + asset/log toggles, collapse rotates 180°).
- **Settings/API config**: two-column (provider list ↔ form), password key inputs, per-provider conditional hints, model-list editor w/ **pull-models + auto-categorize (image/video/LLM) + manual re-categorize**.
- **Asset library panel** (320px right dock): library select, category add/rename, dashed drop-zone, 2-col grid, hover-preview popup, video badge.
- **Smart Canvas Composer**: 520px floating card, engine selector, image/video kind toggle, input thumbs, contenteditable prompt, dynamic param grid, run + cascade-run.

### Interactions / micro-polish (cheap wins worth copying)

- Sidebar hover-expand 80→220px (0.5s), logo rotate on hover.
- Lightbox w/ **compare slider** + copy-prompt + **rerun** buttons.
- Right-click create menu (3-col grid of node types).
- Easing `cubic-bezier(.4,0,.2,1)`, durations .12–.2s; hover lift `translateY(-1px)`.
- Prompt word-counter turns red over limit; copy-button → green "copied".
- i18n via `data-i18n` attributes (we have i18next ✅); EN/ZH; dark/light.
- Lucide icons (we already use lucide-react ✅).

---

## Part 3 — Phased adoption plan

Threaded through: **License** = re-implement, never port. **Nous** = every provider/gen
is a Nous-billed model (credits). **100k** = cloud providers普惠, GPU(ComfyUI/LoRA) =
premium async tier; design for horizontal scale (multi-worker DBOS, hot-table partitioning).

**Phase 1 — Foundation & the consistency engine (the differentiator you asked for)**
1. **Elements/Asset library** (global, cross-project): characters + **scenes + props** + style refs, tags, `@`-reference into shots. _(Catalog C)_
2. **Consistency layer (floor):** multi-reference-image w/ roles + **prompt-template presets** (9-angle / 3-view face / costume / expression / 360 / lighting) + **seed-lock across batch**. _(Catalog D)_
3. **Theming**: dark/light token system + UI scale. _(Part 2)_
4. **Video node** in canvas (wire existing backend). _(Catalog B)_
5. **Loop node** (batch variations). _(Catalog B)_

**Phase 2 — Power surface**
6. **ComfyUI provider** (workflow JSON inject; IPAdapter/ControlNet/LoRA = strong consistency; self-host GPU as premium async tier; multi-instance load-balance). _(Catalog D/E)_
7. **即梦 CLI provider** (cheap video + consistency; subprocess→DBOS). _(Catalog E)_
8. **Image editor** (mask/brush inpaint, outpaint, crop, grid-slice, frame-extract). _(Catalog F)_
9. **LLM node** (reverse-prompt / vision caption) + **Prompt node + library**. _(Catalog B)_
10. **Smart Canvas** (card mode for mainstream users). _(Catalog A)_

**Phase 3 — Pipeline & breadth**
11. **Run-the-graph DAG executor** (DBOS-orchestrated). _(Catalog G)_
12. More providers (ModelScope free + LoRA, RunningHub, Gemini/Volcano). _(Catalog E)_
13. **LTX Director timeline node**, 360 panorama preview, gallery/canvas-browser polish. _(Catalog A/B/F)_
14. Character **LoRA training** (premium). _(Catalog D)_

**Phase 4 — Collab & long tail**
15. Realtime multi-user canvas (Supabase Realtime), cascade run, knife mode. _(Catalog H)_

---

## Part 4 — Guardrails

- **License:** non-commercial reference → ideas/architecture only, clean re-implementation. Theme **token values** and UX patterns are not copyrightable expression of code; still, write our own CSS/components.
- **100k scale:** GPU (ComfyUI/LoRA) never in the free path — async-queued premium, Nous-metered. Cloud providers = primary. Hot tables (assets, gen jobs) partition + TTL. DBOS multi-worker for the gen queue.
- **Cost/billing:** every gen node maps to a Nous model with a credit price; show per-node cost (you already have `resolveModelPriceDisplay`).

---

## Open questions (to settle before sub-specs)

1. **Consistency bar:** ship floor-only (presets + reference-image, all-cloud, simple) first, with ComfyUI/LoRA premium tier deferred to Phase 2 — agree?
2. **Smart Canvas** — is the card-based beginner mode in scope (P1) or later? (Big UX surface; high value at 100k mainstream.)
3. **Single mega-upgrade vs sequential sub-specs:** recommend each Phase-1 item gets its own spec → plan → impl cycle; this doc is the umbrella.
4. **Theming scope:** canvas-only, or roll the dark/light token system app-wide?
5. Which Phase-1 item to spec first? (Recommend: **Elements/Asset library + consistency layer** — your core ask.)
