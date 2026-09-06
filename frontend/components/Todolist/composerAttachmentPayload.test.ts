/**
 * The last hop, where a whole attachment kind can go missing without a sound.
 *
 * `AttachmentRequest` on the backend has every field Optional, so a mapper
 * that drops `asset_id` posts a well-formed request that resolves to nothing
 * and comes back as `asset_not_accessible`. Nothing between the composer and
 * that banner would have said the mapper was at fault — which is why this is
 * a test about FIELD SURVIVAL rather than about the shape of the call.
 *
 * Wire shapes copied from the real producers: ids are strings, `loadout_id`
 * is null for "the asset's default loadout", and `mime`/`url` are the empty
 * strings `toAssetAttachment` emits — not tidied into undefined.
 */
import { describe, it, expect } from 'vitest';

import { toIssueAttachmentPayload } from './composerAttachmentPayload';
import type { ComposerAttachment } from './IssueReplyBox';

const ASSET: ComposerAttachment = {
  kind: 'asset_ref',
  asset_id: '727145299382534201',
  loadout_id: null,
  name: 'Ava',
  mime: '',
  url: '',
};

const RESOURCE: ComposerAttachment = {
  kind: 'resource_ref',
  resource_id: '339710259795355',
  name: 'pitch.mp4',
  mime: 'video/mp4',
  scope: { type: 'personal', id: 'u' },
};

const FILE: ComposerAttachment = {
  kind: 'image',
  url: 'personal/u1/temp/x.png',
  filename: 'x.png',
  size_bytes: 100,
  mime: 'image/png',
  resource_id: 'res-1',
};

describe('toIssueAttachmentPayload', () => {
  it('carries an asset by its asset_id, not as a urlless file', () => {
    expect(toIssueAttachmentPayload([ASSET])).toEqual([
      {
        kind: 'asset_ref',
        asset_id: '727145299382534201',
        loadout_id: null,
        name: 'Ava',
        mime: '',
        url: '',
      },
    ]);
  });

  it('passes a chosen loadout through instead of flattening it', () => {
    // The v2 picker's whole output is this one field. Losing it here would
    // silently re-dress the character in the default outfit.
    const picked = { ...ASSET, loadout_id: '727145299382534512' } as ComposerAttachment;
    const [out] = toIssueAttachmentPayload([picked])!;
    expect(out).toMatchObject({ loadout_id: '727145299382534512' });
  });

  it('keeps a resource ref identified by resource_id and scope', () => {
    expect(toIssueAttachmentPayload([RESOURCE])).toEqual([
      {
        kind: 'resource_ref',
        resource_id: '339710259795355',
        name: 'pitch.mp4',
        mime: 'video/mp4',
        scope: { type: 'personal', id: 'u' },
      },
    ]);
  });

  it('reduces an uploaded file to url + mime, dropping composer-only fields', () => {
    // `filename` / `size_bytes` / `resource_id` are what the CHIP painted
    // from; the backend reads the url.
    expect(toIssueAttachmentPayload([FILE])).toEqual([
      { kind: 'image', url: 'personal/u1/temp/x.png', mime: 'image/png' },
    ]);
  });

  it('keeps all three kinds, in order, in one turn', () => {
    const kinds = toIssueAttachmentPayload([FILE, RESOURCE, ASSET])!.map((a) => a.kind);
    expect(kinds).toEqual(['image', 'resource_ref', 'asset_ref']);
  });

  it('omits the key entirely rather than posting an empty list', () => {
    // `[]` on the wire reads as "an explicit empty exclusion", a different
    // statement from "no attachments".
    expect(toIssueAttachmentPayload([])).toBeUndefined();
  });
});
