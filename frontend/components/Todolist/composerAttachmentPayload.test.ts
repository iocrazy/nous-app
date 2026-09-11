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

/**
 * Citations (harness 3a Task 6).
 *
 * Wire shape copied from `output_ref_resolver._stamped`, which is what the
 * backend STORES: `kind / ref_kind / ref_id / version / title`. Note `title`,
 * not `name` — every other reference kind names its snapshot `name`, and a
 * mapper that reached for the familiar field would post an attachment whose
 * every required coordinate is present except the one the resolver reads for
 * the chip's words. `ref_id` is a STRING (Snowflake).
 */
describe('toIssueAttachmentPayload — output citations', () => {
  const CITATION: ComposerAttachment = {
    kind: 'output_ref',
    ref_kind: 'script_shot',
    ref_id: '727145299382534999',
    version: 2,
    title: 'S3 · Shot #1',
  };

  it('maps an output reference by kind, id and version', () => {
    // Missing this branch does not throw: `AttachmentRequest` has every field
    // Optional, so the server accepts `{kind:'output_ref'}` with no
    // coordinates and refuses it as `output_ref_unresolvable` — a refusal the
    // user reads as "the thing I pointed at is gone".
    expect(toIssueAttachmentPayload([CITATION])).toEqual([
      {
        kind: 'output_ref',
        ref_kind: 'script_shot',
        ref_id: '727145299382534999',
        version: 2,
        title: 'S3 · Shot #1',
      },
    ]);
  });

  it('keeps the version that was cited, not the object', () => {
    // A citation is version-locked on purpose: the object may be revised
    // after the comment is posted, and the comment still means v2.
    const [out] = toIssueAttachmentPayload([{ ...CITATION, version: 1 }])!;
    expect(out).toMatchObject({ ref_id: '727145299382534999', version: 1 });
  });

  it('does not fall through to the file branch', () => {
    // The failure this guards: the trailing `return {kind, url, mime}` accepts
    // any unrecognised kind, so a forgotten branch posts `{kind:'output_ref',
    // url: undefined}` — well-formed, accepted, and pointing at nothing.
    const [out] = toIssueAttachmentPayload([CITATION])!;
    expect(out).not.toHaveProperty('url');
  });

  it('carries citations alongside the other three kinds, in order', () => {
    const kinds = toIssueAttachmentPayload([FILE, RESOURCE, ASSET, CITATION])!.map((a) => a.kind);
    expect(kinds).toEqual(['image', 'resource_ref', 'asset_ref', 'output_ref']);
  });
});
