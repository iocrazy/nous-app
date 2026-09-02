// frontend/components/resources/assets/sheet/assetSheetFixtures.ts
//
// Real `AssetDetailResponse` payloads for the sheet's tests.
//
// Shapes are copied from
// `.superpowers/sdd/2026-08-29-asset-library-p2-codex-and-sheets/wire-fixtures-assets.json`,
// which is the backend's own serializer output: every BIGINT id is a JSON
// STRING (`_serialize` calls `str()` at the repository boundary, arrays of ids
// included), `scope_id` is null ONLY on a system preset, `file_counts_by_slot`
// is sparse, and `tags` is an object of group → values rather than a flat
// array. Idealising any of those is the 2026-08-12 lesson (CLAUDE.md:
// "边界 mock 必须用真实 JSON 形状").

import type {
  AssetFileRow,
  AssetLinkRow,
  AssetLoadoutRow,
  AssetRow,
  AssetRowDetail,
  AssetType,
} from '../../../../services/assetsService';

const SCOPE = '727145299382534200';

export const SHEET_SCOPE_ID = SCOPE;

export function makeRow(overrides: Partial<AssetRow> & { id: string }): AssetRow {
  return {
    scope_id: SCOPE,
    asset_type: 'character' as AssetType,
    subtype: null,
    name: 'Sang Yao',
    role_tag: 'lead',
    description: 'Late twenties, wind-burnt.',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    in_library: true,
    tags: { role: ['lead'] },
    sort_order: 0,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-08-29T10:00:00Z',
    updated_at: '2026-08-29T10:00:00Z',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 0,
    ...overrides,
  };
}

export function makeFile(overrides: Partial<AssetFileRow> & { resource_id: string }): AssetFileRow {
  return {
    asset_id: '727145299382534300',
    slot: 'sheet',
    loadout_id: null,
    sort_order: 0,
    note: null,
    attached_by: null,
    attached_at: '2026-08-29T10:00:00Z',
    ...overrides,
  };
}

export function makeLink(overrides: Partial<AssetLinkRow>): AssetLinkRow {
  return {
    from_asset_id: '727145299382534300',
    to_asset_id: '727145299382534310',
    relation: 'wears',
    created_at: null,
    ...overrides,
  };
}

export function makeLoadout(
  overrides: Partial<AssetLoadoutRow> & { id: string },
): AssetLoadoutRow {
  return {
    asset_id: '727145299382534300',
    name: 'Default',
    is_default: true,
    costume_ids: [],
    prop_ids: [],
    prompt_extra: null,
    sort_order: 0,
    created_at: '2026-08-29T10:00:00Z',
    ...overrides,
  };
}

export function makeDetail(
  overrides: Partial<AssetRowDetail> & { id: string },
): AssetRowDetail {
  const { files, links, linked_by, loadouts, ...row } = overrides;
  return {
    ...makeRow(row as Partial<AssetRow> & { id: string }),
    files: files ?? [],
    links: links ?? [],
    linked_by: linked_by ?? [],
    loadouts: loadouts ?? [],
  };
}

/**
 * The character sheet the tests centre on — the wire fixture's
 * `GET /assets/{id}` row, widened with the extra files a Board needs to be
 * worth testing: one sheet (primary), two stills, and two `worn` files that
 * belong to DIFFERENT loadouts plus one that belongs to none.
 */
export const CHARACTER_DETAIL: AssetRowDetail = makeDetail({
  id: '727145299382534300',
  name: 'Sang Yao',
  cover_file_id: '727145299382534146',
  prompt_positive: 'same woman as reference…',
  file_counts_by_slot: { sheet: 1, stills: 2, worn: 3 },
  project_ids: ['55'],
  loadout_count: 2,
  files: [
    makeFile({ resource_id: '727145299382534146', slot: 'sheet' }),
    makeFile({ resource_id: '727145299382534150', slot: 'stills', sort_order: 1 }),
    makeFile({ resource_id: '727145299382534151', slot: 'stills', sort_order: 0 }),
    makeFile({
      resource_id: '727145299382534160',
      slot: 'worn',
      loadout_id: '727145299382534400',
    }),
    makeFile({
      resource_id: '727145299382534161',
      slot: 'worn',
      loadout_id: '727145299382534401',
    }),
    makeFile({ resource_id: '727145299382534162', slot: 'worn', loadout_id: null }),
  ],
  links: [makeLink({ to_asset_id: '727145299382534310', relation: 'wears' })],
  linked_by: [],
  loadouts: [
    makeLoadout({ id: '727145299382534400', name: 'Default', is_default: true }),
    makeLoadout({
      id: '727145299382534401',
      name: 'Night raid',
      is_default: false,
      costume_ids: ['727145299382534310'],
      prompt_extra: 'black hooded night robe',
      sort_order: 1,
    }),
  ],
});

/** The costume the character wears — the other end of the link above. */
export const COSTUME_DETAIL: AssetRowDetail = makeDetail({
  id: '727145299382534310',
  asset_type: 'costume',
  name: 'Night Robe',
  role_tag: '',
  prompt_positive: 'black hooded robe, matte weave',
  prompt_negative: 'shiny, satin',
  file_counts_by_slot: { flat: 1 },
  readiness: { state: 'ready', missing: [] },
  files: [makeFile({ resource_id: '727145299382534170', slot: 'flat' })],
  linked_by: [makeLink({ from_asset_id: '727145299382534300', relation: 'wears' })],
});

/** A read-only global preset: `scope_id` null, `is_system_preset` true. */
export const PRESET_PROMPT_DETAIL: AssetRowDetail = makeDetail({
  id: '727145299382534303',
  scope_id: null,
  asset_type: 'prompt',
  name: 'Multi-angle 3x3 sheet',
  role_tag: '',
  source: 'system_preset',
  is_system_preset: true,
  in_library: true,
  prompt_positive: 'same woman as reference…',
  attrs: { placeholders: { subject: 'who the sheet is of' } },
  platform_params: { aspect_ratio: '1:1' },
  file_counts_by_slot: {},
  project_ids: [],
});

/** An audio asset with a subtype, so its relation section is addable. */
export const AUDIO_DETAIL: AssetRowDetail = makeDetail({
  id: '727145299382534320',
  asset_type: 'audio',
  subtype: 'sfx',
  name: 'Grove Ambience',
  role_tag: '',
  attrs: { loopable: true, duration_sec: 92 },
  file_counts_by_slot: { primary: 1, variants: 2 },
  files: [
    makeFile({ resource_id: '727145299382534180', slot: 'primary' }),
    makeFile({ resource_id: '727145299382534181', slot: 'variants' }),
    makeFile({ resource_id: '727145299382534182', slot: 'variants', sort_order: 1 }),
  ],
  links: [
    makeLink({
      from_asset_id: '727145299382534320',
      to_asset_id: '727145299382534302',
      relation: 'ambience_of',
    }),
  ],
});
