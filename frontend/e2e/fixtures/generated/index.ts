// frontend/e2e/fixtures/generated/index.ts
//
// Wire bodies for the Generated inbox e2e run.
//
// PROVENANCE — this matters, and the two halves of this file do NOT have the
// same one (see CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状"):
//
//  * `generated-wire.json` is a VERBATIM copy of the bodies captured off the
//    real response models while `backend/tests/api/test_generated_router.py`
//    exercised each route (the capture is kept alongside the plan as
//    `.superpowers/sdd/2026-08-29-asset-library-p1-generated-inbox/`
//    `wire-fixtures.json`). Every key in it names the endpoint it came from.
//    Do not "tidy" its values: `id` / `scope_id` / `canvas_id` /
//    `promoted_resource_id` / `source_asset_id` are snowflakes serialised as
//    JSON **strings** by `GeneratedItem`, `created_at` is an ISO string, and
//    `source` is always a full object (never null) with `deep_link` nullable.
//
//  * The `/api/v1/assets` bodies below were NOT captured from a router test —
//    that suite fakes its service, so its bodies are not the real shape. They
//    are built from what actually produces them in production:
//    `AssetsService._derived` → `with_derived` (adds `readiness`,
//    `file_counts_by_slot`, `project_ids`, `loadout_count`) →
//    `assets_repository._serialize`, which stringifies exactly
//    `("id", "scope_id", "cover_file_id", "duplicated_from")` plus
//    `created_by`, and renders datetimes as ISO strings. Every other column of
//    `app/models/assets.py::Assets` is passed through untouched, which is why
//    the nullable ones are present-and-null here rather than absent.
//
// If a backend response model changes, the fix is to re-capture, not to edit
// the values here until the test goes green again.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** The captured bodies, keyed by endpoint exactly as the capture named them. */
const wire = JSON.parse(
  fs.readFileSync(path.join(HERE, 'generated-wire.json'), 'utf8'),
) as Record<string, { success: boolean; data?: unknown; error?: unknown }>;

export interface WireGeneratedItem {
  id: string;
  scope_id: string;
  media_kind: string;
  mime: string;
  prompt: string;
  model: string;
  provider: string;
  origin_kind: string;
  canvas_id: string | null;
  node_id: string | null;
  created_at: string;
  promoted_resource_id: string | null;
  review_state: 'unreviewed' | 'saved' | 'in_assets';
  source_asset_id: string | null;
  source: {
    kind: string;
    label: string;
    canvas_id: string | null;
    node_id: string | null;
    shot_id: string | null;
    conversation_id: string | null;
    deep_link: string | null;
  };
  title: string;
}

/** The three items the capture carries: one per review state. */
export const ITEMS = (wire['GET /generated'].data as { items: WireGeneratedItem[] }).items;

export const UNREVIEWED = ITEMS[0]; // canvas_run, has source_asset_id
export const SAVED = ITEMS[1]; // chat_upload
export const IN_ASSETS = ITEMS[2]; // shot_generate

/** The scope every captured row belongs to — also the team in the test URL. */
export const SCOPE_ID = UNREVIEWED.scope_id;

/** The asset `UNREVIEWED.source_asset_id` points at: the dialog's suggestion. */
export const SUGGESTED_ASSET_ID = UNREVIEWED.source_asset_id as string;

export const COUNTS = wire['GET /generated/counts'].data as {
  unreviewed: number;
  saved: number;
  in_assets: number;
};

export const SAVE_AS_ASSET_201 = wire['POST /generated/{id}/save-as-asset (201)'].data as {
  generation: WireGeneratedItem;
  asset_id: string;
  resource_id: string;
};

export const CLEANUP_DRY = wire['POST /generated/cleanup (dry)'].data as {
  dry_run: boolean;
  count: number;
  sample: WireGeneratedItem[];
  deleted: number;
  truncated: boolean;
};

// ─── /api/v1/assets (shape derived, see PROVENANCE above) ───────────────────

/** Every column of `assets` in `_serialize`'s output shape, plus the four
 *  derived fields. Snowflakes are strings; the rest keeps its native type. */
function assetRow(over: Partial<Record<string, unknown>> = {}): Record<string, unknown> {
  return {
    id: SUGGESTED_ASSET_ID,
    scope_id: SCOPE_ID,
    asset_type: 'character',
    subtype: null,
    name: 'Sang Yao',
    role_tag: null,
    description: null,
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'generated',
    duplicated_from: null,
    is_system_preset: false,
    tags: [],
    sort_order: 0,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-08-20T12:00:00+00:00',
    updated_at: '2026-08-20T12:00:00+00:00',
    deleted_at: null,
    readiness: { state: 'draft', missing: ['sheet'] },
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 1,
    ...over,
  };
}

/** The suggestion, as `GET /assets/{id}` returns it: the row plus the four
 *  relation lists `get_asset` attaches. */
export const SUGGESTED_ASSET_DETAIL = {
  ...assetRow(),
  files: [],
  links: [],
  linked_by: [],
  loadouts: [
    {
      id: '727145299382534301',
      asset_id: SUGGESTED_ASSET_ID,
      name: 'Default',
      is_default: true,
      notes: null,
      created_at: '2026-08-20T12:00:00+00:00',
      updated_at: '2026-08-20T12:00:00+00:00',
    },
  ],
};

/** `GET /assets?type=character` — the suggestion plus one other character, so
 *  the pinned-first ordering and the de-duplication both have something to be
 *  wrong about. */
export const ASSET_LIST = [
  assetRow({
    id: '727145299382534888',
    name: 'Harbour Master',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: { sheet: 2 },
  }),
  assetRow(),
];

/** 1×1 transparent PNG — the cover byte stream for `<img>`/`poster`. */
export const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);
