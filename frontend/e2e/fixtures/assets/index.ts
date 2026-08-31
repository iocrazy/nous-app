// frontend/e2e/fixtures/assets/index.ts
//
// Wire bodies for the asset codex e2e run.
//
// PROVENANCE — the three groups below do NOT share one, and the difference is
// the whole point (CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状"):
//
//  1. `assets-wire.json` is a VERBATIM copy of the capture kept alongside the
//     plan as `.superpowers/sdd/2026-08-29-asset-library-p2-codex-and-sheets/`
//     `wire-fixtures-assets.json`. Every key names the endpoint it came from.
//     Do not "tidy" its values. `assets_repository._serialize` stringifies
//     exactly `("id", "scope_id", "cover_file_id", "duplicated_from")` plus
//     `created_by`, renders datetimes as ISO strings, and passes every other
//     column of `app/models/assets.py::Assets` through untouched — which is
//     why the nullable ones are present-and-null here rather than absent.
//     `readiness` / `file_counts_by_slot` / `project_ids` / `loadout_count`
//     are folded on afterwards by `AssetsService._derived` → `with_derived`.
//
//  2. The DRAFT asset's detail body is DERIVED, not captured: the capture only
//     covers the ready character. It is the list row (group 1) plus the four
//     relation arrays `AssetResponse` adds on the by-id route, built by the
//     same rule the router follows — see `DRAFT_DETAIL` below.
//
//  3. `SEARCH_RESULTS` stands in for `GET /api/v1/resources/search`, which is
//     a DIFFERENT contract on purpose: that router returns a BARE object, not
//     the `{success, data}` envelope this one uses, and it builds its rows
//     from an explicit whitelist in `resources_search_router.py` (ids and
//     `scope.id` `str()`-ed there). Wrapping it in an envelope here would make
//     a client that mishandled the real bare body pass.
//
// If a backend response model changes, the fix is to re-capture, not to edit
// the values here until the test goes green again.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const wire = JSON.parse(
  fs.readFileSync(path.join(HERE, 'assets-wire.json'), 'utf8'),
) as Record<string, { success: boolean; data?: unknown; error?: unknown }>;

/** One row of `GET /assets`. Only the fields this spec reads are named. */
export interface WireAsset {
  id: string;
  scope_id: string | null;
  asset_type: 'character' | 'location' | 'prop' | 'costume' | 'prompt' | 'audio';
  name: string;
  role_tag: string;
  prompt_positive: string | null;
  cover_file_id: string | null;
  is_system_preset: boolean;
  readiness: { state: 'ready' | 'draft'; missing: string[] };
  file_counts_by_slot: Record<string, number>;
  project_ids: string[];
  loadout_count: number;
  [key: string]: unknown;
}

export interface WireLoadout {
  id: string;
  asset_id: string;
  name: string;
  is_default: boolean;
  costume_ids: string[];
  prop_ids: string[];
  prompt_extra: string | null;
  sort_order: number;
  created_at: string;
}

export interface WireAssetDetail extends WireAsset {
  files: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  linked_by: Array<Record<string, unknown>>;
  loadouts: WireLoadout[];
}

export const ASSETS = wire['GET /assets'].data as WireAsset[];

/** Sang Yao — character, `ready`, has a `sheet` file and two loadouts. */
export const READY = ASSETS[0];
/** Fan Qi — character, `draft`, missing `sheet`. The one the equip run flips. */
export const DRAFT = ASSETS[1];
/** Bamboo Grove — location. Proves the type tabs actually narrow the request. */
export const LOCATION = ASSETS[2];
/** A global system preset: `scope_id` null, read-only from every scope. */
export const PRESET = ASSETS[3];

export const SCOPE_ID = READY.scope_id as string;

export const READY_DETAIL = wire['GET /assets/{id}'].data as WireAssetDetail;

/**
 * `GET /assets/{DRAFT.id}` — derived, see PROVENANCE note 2.
 *
 * The by-id route answers the same row shape as the list plus `files` /
 * `links` / `linked_by` / `loadouts`. Fan Qi's list row carries
 * `file_counts_by_slot: {stills: 1}` and `loadout_count: 1`, so the detail has
 * to agree: exactly one file (on `stills`, since `sheet` is what
 * `readiness.missing` says is absent) and exactly one loadout. A detail body
 * whose arrays contradicted its own derived counts would be a shape the
 * backend cannot produce, and the sheet would be tested against fiction.
 *
 * `loadout_id: null` on the file is not filler either: only `worn` is
 * loadout-scoped (`assetSheetModel.loadoutForSlot`), so a `stills` row with a
 * loadout id would be a placement the backend never writes.
 */
export const DRAFT_DEFAULT_LOADOUT: WireLoadout = {
  id: '727145299382534410',
  asset_id: DRAFT.id,
  name: 'Default',
  is_default: true,
  costume_ids: [],
  prop_ids: [],
  prompt_extra: null,
  sort_order: 0,
  created_at: '2026-08-29T10:00:00Z',
};

export const DRAFT_DETAIL: WireAssetDetail = {
  ...DRAFT,
  files: [
    {
      asset_id: DRAFT.id,
      resource_id: '727145299382534147',
      slot: 'stills',
      loadout_id: null,
      sort_order: 0,
      note: null,
      attached_by: null,
      attached_at: '2026-08-29T10:00:00Z',
    },
  ],
  links: [],
  linked_by: [],
  loadouts: [DRAFT_DEFAULT_LOADOUT],
};

/** The id `POST /assets/{id}/duplicate` hands back, and the copy's name. */
export const DUPLICATE_ID = '727145299382534500';
export const DUPLICATE_NAME = `${DRAFT.name} copy`;

/** The loadout `POST /assets/{id}/loadouts` creates during the spec. */
export const NEW_LOADOUT_ID = '727145299382534411';
export const NEW_LOADOUT_NAME = 'Night Raid';

/**
 * `GET /api/v1/resources/search` — BARE body, no envelope (see PROVENANCE 3).
 *
 * `id` and `scope.id` are strings because that router `str()`s them
 * explicitly; `thumbnail_url` is the RELATIVE path it emits, which the client
 * has to prefix itself. `counts` and `next_cursor` are present because
 * `useResourceSearch` reads `results` off a body it only shape-guards on that
 * one key — a fixture missing the rest would not fail, and that is exactly the
 * kind of divergence this file exists to prevent.
 */
export const SEARCH_RESULTS = {
  results: [
    {
      id: '727145299382534148',
      name: 'sang-yao-sheet-3x3.png',
      kind: 'image' as const,
      mime: 'image/png',
      size: 812_004,
      scope: { type: 'team' as const, id: SCOPE_ID },
      updated_at: '2026-08-29T09:00:00Z',
      thumbnail_url: '/api/v1/resources/727145299382534148/cover',
      transcript_status: null,
      summary_status: null,
    },
    {
      id: '727145299382534149',
      name: 'fan-qi-turnaround.png',
      kind: 'image' as const,
      mime: 'image/png',
      size: 640_112,
      scope: { type: 'team' as const, id: SCOPE_ID },
      updated_at: '2026-08-29T08:00:00Z',
      thumbnail_url: '/api/v1/resources/727145299382534149/cover',
      transcript_status: null,
      summary_status: null,
    },
  ],
  counts: { all: 2, video: 0, image: 2, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

/** 1×1 transparent PNG for the cover endpoints the cards and pins hit. */
export const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);
