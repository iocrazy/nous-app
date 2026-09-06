/**
 * Staged ASSETS (P5) — the composer's second holding area.
 *
 * What these pin is the boundary: exactly which fields cross it, which
 * snapshots deliberately do not, and that one asset is sent once. The wire
 * shape here is the contract `AttachmentRequest` reads on the other side
 * (`kind='asset_ref'` + `asset_id` + `loadout_id` + `name`), so a field added
 * or renamed in passing shows up as a failing equality, not as a reference the
 * backend silently fails to resolve.
 *
 * Ids are STRINGS throughout, as the assets router emits them (`str(...)` on
 * every id). `scope_id` is null on a system preset — copied from the real
 * `GET /assets` fixture rather than tidied into a string.
 */
import { describe, it, expect } from 'vitest';
import {
  mergeAssetAttachments,
  removeStagedAsset,
  setStagedAssetLoadout,
  stageAsset,
  toAssetAttachment,
  toStagedAsset,
  type StagedAssetRef,
} from './stagedResources';

/** A real `GET /assets` row, trimmed to the fields this path reads. */
const CHARACTER = {
  id: '727145299382534300',
  name: 'Sang Yao',
  asset_type: 'character',
  cover_file_id: '727145299382534146',
  scope_id: '727145299382534200',
};

/** A system preset: `scope_id` is null on the wire, and it has no cover. */
const PRESET = {
  id: '727145299382534303',
  name: 'Multi-angle 3x3 sheet',
  asset_type: 'prompt',
  cover_file_id: null,
  scope_id: null,
};

describe('toStagedAsset', () => {
  it('keeps both halves: the wire fields and the chip snapshot', () => {
    expect(toStagedAsset(CHARACTER)).toEqual({
      asset_id: '727145299382534300',
      loadout_id: null,
      // v2's chip label. Null until the loadout menu sets it, and it never
      // reaches the wire — see the `toAssetAttachment` key-set test below.
      loadout_name: null,
      name: 'Sang Yao',
      asset_type: 'character',
      cover_file_id: '727145299382534146',
      scope_id: '727145299382534200',
    });
  });

  it('normalises absent snapshot fields to empty strings', () => {
    // '' means "claims nothing" — the chip draws its type icon rather than
    // asking the cover route about a resource that does not exist.
    const staged = toStagedAsset(PRESET);
    expect(staged.cover_file_id).toBe('');
    expect(staged.scope_id).toBe('');
  });

  it('preserves a null loadout_id rather than flattening it', () => {
    // null is a MEANING here — the backend reads it as "use the default
    // loadout" — so it is the one field absent must not become ''.
    expect(toStagedAsset(CHARACTER).loadout_id).toBeNull();
    expect(toStagedAsset({ ...CHARACTER, loadout_id: '900' }).loadout_id).toBe('900');
  });

  it('stringifies a numeric id rather than trusting the caller', () => {
    // The router sends strings, but a caller that read an id off a JSON
    // number would poison every later comparison (dedupe, remove, merge).
    const staged = toStagedAsset({ ...CHARACTER, id: 727145299382534300 as unknown as string });
    expect(staged.asset_id).toBe('727145299382534300');
  });
});

describe('stageAsset / removeStagedAsset', () => {
  it('appends an asset the row does not already hold', () => {
    const next = stageAsset([], CHARACTER);
    expect(next.map((s) => s.asset_id)).toEqual(['727145299382534300']);
  });

  it('refuses a duplicate — a double-click is one intent', () => {
    const once = stageAsset([], CHARACTER);
    expect(stageAsset(once, CHARACTER)).toHaveLength(1);
  });

  it('ignores an item with no id instead of staging a blank chip', () => {
    expect(stageAsset([], { id: '' })).toEqual([]);
  });

  it('drops exactly the asset the × was clicked on', () => {
    const list = stageAsset(stageAsset([], CHARACTER), PRESET);
    expect(removeStagedAsset(list, CHARACTER.id).map((s) => s.asset_id)).toEqual([
      '727145299382534303',
    ]);
  });
});

describe('setStagedAssetLoadout — the v2 pick', () => {
  it('moves the id and the name together', () => {
    // Separately would let the chip say one outfit while another goes on the
    // wire — the exact disagreement the label exists to rule out.
    const list = [toStagedAsset(CHARACTER)];
    const next = setStagedAssetLoadout(list, CHARACTER.id, '900', 'Rainy Night');
    expect(next[0].loadout_id).toBe('900');
    expect(next[0].loadout_name).toBe('Rainy Night');
  });

  it('clears both for the default-loadout entry', () => {
    const picked = setStagedAssetLoadout(
      [toStagedAsset(CHARACTER)],
      CHARACTER.id,
      '900',
      'Rainy Night',
    );
    const cleared = setStagedAssetLoadout(picked, CHARACTER.id, null, null);
    expect(cleared[0].loadout_id).toBeNull();
    expect(cleared[0].loadout_name).toBeNull();
  });

  it('returns the untouched assets by identity', () => {
    // A menu on one chip must not re-render the rest of the row.
    const list = [toStagedAsset(CHARACTER), toStagedAsset(PRESET)];
    const next = setStagedAssetLoadout(list, CHARACTER.id, '900', 'Rainy Night');
    expect(next[1]).toBe(list[1]);
  });

  it('changes nothing when no staged asset carries that id', () => {
    const list = [toStagedAsset(CHARACTER)];
    expect(setStagedAssetLoadout(list, 'not-staged', '900', 'X')).toEqual(list);
  });
});

describe('toAssetAttachment — the wire shape', () => {
  it('leaves the loadout NAME behind while sending the id', () => {
    // The server re-reads the name from the row it owns; sending ours would
    // assert something it ignores, and could disagree with it after a rename.
    const staged = setStagedAssetLoadout(
      [toStagedAsset(CHARACTER)],
      CHARACTER.id,
      '900',
      'Rainy Night',
    )[0];
    const wire = toAssetAttachment(staged) as Record<string, unknown>;
    expect(wire.loadout_id).toBe('900');
    expect(wire).not.toHaveProperty('loadout_name');
  });

  it('sends the four contract fields plus the two empty ones, and nothing else', () => {
    expect(toAssetAttachment(toStagedAsset(CHARACTER))).toEqual({
      kind: 'asset_ref',
      asset_id: '727145299382534300',
      loadout_id: null,
      name: 'Sang Yao',
      mime: '',
      url: '',
    });
  });

  it('leaves the composer-side snapshots behind', () => {
    // `asset_type` / `cover_file_id` / `scope_id` are what the CHIP paints
    // from. The server re-resolves the asset by id and re-checks access by
    // team membership, so sending them would assert something it ignores.
    const wire = toAssetAttachment(toStagedAsset(CHARACTER)) as Record<string, unknown>;
    expect(Object.keys(wire).sort()).toEqual([
      'asset_id',
      'kind',
      'loadout_id',
      'mime',
      'name',
      'url',
    ]);
  });
});

describe('mergeAssetAttachments', () => {
  it('dedupes by asset_id — one asset is resolved and billed once', () => {
    const twice: StagedAssetRef[] = [toStagedAsset(CHARACTER), toStagedAsset(CHARACTER)];
    expect(mergeAssetAttachments(twice)).toHaveLength(1);
  });

  it('keeps distinct assets in the order they were staged', () => {
    const list = [toStagedAsset(CHARACTER), toStagedAsset(PRESET)];
    expect(mergeAssetAttachments(list).map((a) => a.asset_id)).toEqual([
      '727145299382534300',
      '727145299382534303',
    ]);
  });

  it('returns an empty list when nothing is staged', () => {
    expect(mergeAssetAttachments([])).toEqual([]);
  });
});
