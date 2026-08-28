import { beforeEach, describe, expect, it } from 'vitest';

import { clearPublishDraft, draftHasContent, readPublishDraft, writePublishDraft } from './publishDraft';

beforeEach(() => localStorage.clear());

describe('publishDraft', () => {
  it('round-trips the fields worth keeping, per scope', () => {
    writePublishDraft('s1', { title: 'Hello', selectedVideos: ['900'], covers: { vertical: '7001' } });
    const d = readPublishDraft('s1');
    expect(d?.title).toBe('Hello');
    expect(d?.selectedVideos).toEqual(['900']);
    expect(d?.covers?.vertical).toBe('7001');
    expect(d?.savedAt).toBeTruthy();
    expect(readPublishDraft('s2')).toBeNull();
  });

  it('an empty form is not a draft — writing it removes any old one', () => {
    writePublishDraft('s1', { title: 'x' });
    writePublishDraft('s1', { title: '   ', selectedVideos: [] });
    expect(readPublishDraft('s1')).toBeNull();
    expect(draftHasContent({ covers: { horizontal: '1' } })).toBe(true);
  });

  it('clear forgets it', () => {
    writePublishDraft('s1', { description: 'd' });
    clearPublishDraft('s1');
    expect(readPublishDraft('s1')).toBeNull();
  });
});
