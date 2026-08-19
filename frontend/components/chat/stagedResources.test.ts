/**
 * Staged resources — the composer now has TWO places a library asset can be
 * sitting when Enter is pressed: the attachment row (everything picked since
 * this change) and inline chips a restored draft still carries. These tests
 * pin the part that must not drift: what crosses the wire is byte-identical
 * to what the inline-only path used to send, and one asset is sent once.
 */
import { describe, it, expect } from 'vitest';
import {
  toStagedResource,
  stageResource,
  removeStagedResource,
  toRefAttachment,
  mergeRefAttachments,
  type StagedResourceRef,
} from './stagedResources';
import type { ResourceRefAttachment } from '../../types';

/** A real `/resources/search` row (the @ picker path). */
const SEARCH_ROW = {
  id: '339710259795355',
  name: 'pitch.mp4',
  kind: 'video',
  mime: 'video/mp4',
  scope: { type: 'team' as const, id: 't1' },
  thumbnail_url: '/api/v1/resources/339710259795355/cover',
  transcript_status: 'processing',
  summary_status: 'none',
};

function staged(over: Partial<StagedResourceRef> = {}): StagedResourceRef {
  return { ...toStagedResource(SEARCH_ROW), ...over };
}

describe('toStagedResource', () => {
  it('keeps the snapshot fields the chip paints from', () => {
    expect(toStagedResource(SEARCH_ROW)).toEqual({
      resource_id: '339710259795355',
      name: 'pitch.mp4',
      kind: 'video',
      mime: 'video/mp4',
      scope: { type: 'team', id: 't1' },
      thumbnail_url: '/api/v1/resources/339710259795355/cover',
      transcript_status: 'processing',
      summary_status: 'none',
    });
  });

  it('normalises the context-menu case, which has no status columns', () => {
    // Absent must become '' rather than stay undefined: the status helpers
    // read '' as "claims nothing", and claiming "not processed yet" about a
    // finished video is the exact lie resourceStatus.ts was written to avoid.
    expect(toStagedResource({ id: '5', name: 'note.md', kind: 'doc' })).toMatchObject({
      mime: '',
      thumbnail_url: '',
      transcript_status: '',
      summary_status: '',
      scope: { type: 'personal', id: '' },
    });
  });
});

describe('stageResource', () => {
  it('appends a new asset', () => {
    const list = stageResource([], SEARCH_ROW);
    expect(list).toHaveLength(1);
    expect(list[0].resource_id).toBe('339710259795355');
  });

  it('ignores a repeat of the same asset instead of showing two chips', () => {
    const once = stageResource([], SEARCH_ROW);
    const twice = stageResource(once, SEARCH_ROW);
    expect(twice).toHaveLength(1);
    // Same array identity → React skips the re-render too.
    expect(twice).toBe(once);
  });

  it('keeps distinct assets apart', () => {
    const list = stageResource(stageResource([], SEARCH_ROW), { ...SEARCH_ROW, id: '42' });
    expect(list.map((s) => s.resource_id)).toEqual(['339710259795355', '42']);
  });

  it('refuses an item with no id rather than staging an unsendable chip', () => {
    expect(stageResource([], { id: '', name: 'ghost', kind: 'doc' })).toHaveLength(0);
  });
});

describe('removeStagedResource', () => {
  it('drops only the named asset', () => {
    const list = [staged(), staged({ resource_id: '42', name: 'b.mp4' })];
    expect(removeStagedResource(list, '42').map((s) => s.name)).toEqual(['pitch.mp4']);
  });
});

describe('wire shape', () => {
  it('emits exactly the five ResourceRefAttachment fields, unchanged', () => {
    // The backend contract predates this change and must not notice it.
    const ref = toRefAttachment(staged());
    expect(ref).toEqual({
      kind: 'resource_ref',
      resource_id: '339710259795355',
      name: 'pitch.mp4',
      mime: 'video/mp4',
      scope: { type: 'team', id: 't1' },
    });
    // Snapshot-only fields are for the chip, not the wire.
    expect(Object.keys(ref).sort()).toEqual(
      ['kind', 'mime', 'name', 'resource_id', 'scope'],
    );
  });
});

describe('mergeRefAttachments', () => {
  const inlineChip: ResourceRefAttachment = {
    kind: 'resource_ref',
    resource_id: '900',
    name: 'draft.md',
    mime: 'text/markdown',
    scope: { type: 'personal', id: 'u' },
  };

  it('sends both sources — a restored draft chip AND the attachment row', () => {
    const merged = mergeRefAttachments([inlineChip], [staged()]);
    expect(merged.map((r) => r.resource_id)).toEqual(['900', '339710259795355']);
  });

  it('sends an asset once when it is in both places', () => {
    // Double-sending means the backend resolves and bills it twice.
    const dup: ResourceRefAttachment = { ...inlineChip, resource_id: '339710259795355' };
    const merged = mergeRefAttachments([dup], [staged()]);
    expect(merged).toHaveLength(1);
    expect(merged[0].name).toBe('draft.md'); // inline kept its position
  });

  it('is empty when nothing is attached at all', () => {
    expect(mergeRefAttachments([], [])).toEqual([]);
  });

  it('carries the staged asset through untouched when there are no chips', () => {
    expect(mergeRefAttachments([], [staged()])).toEqual([toRefAttachment(staged())]);
  });
});
