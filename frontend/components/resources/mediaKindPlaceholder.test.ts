// frontend/components/resources/mediaKindPlaceholder.test.ts
//
// The module three views now share. Its whole purpose is that the grid card,
// the cleanup preview and the lightbox cannot disagree about which rows have
// no picture — which is only true while the list below is the single source
// the guard is derived from, not a literal each consumer re-spells.

import { describe, expect, it } from 'vitest';

import {
  NON_VISUAL_MEDIA,
  NON_VISUAL_MEDIA_KINDS,
  isNonVisualMediaKind,
  mediaFormatLabel,
  placeholderFor,
} from './mediaKindPlaceholder';

describe('the non-visual kinds', () => {
  it('are exactly the two the backend writes with nothing to show', () => {
    // `audio` from the As Asset mint, `file` from a chat upload that is
    // neither image nor video. Adding a third belongs in the array, and this
    // line is where a reviewer is asked to agree it has no preview.
    expect([...NON_VISUAL_MEDIA_KINDS]).toEqual(['audio', 'file']);
  });

  it('every listed kind has an icon — the guard cannot outrun the map', () => {
    for (const kind of NON_VISUAL_MEDIA_KINDS) {
      expect(isNonVisualMediaKind(kind)).toBe(true);
      expect(placeholderFor(kind)?.Icon).toBeTruthy();
      expect(NON_VISUAL_MEDIA[kind].labelKey).toMatch(/^generated\.type\./);
    }
  });

  it.each(['image', 'video', 'hologram', '', null, undefined])(
    'leaves %s on the visual path',
    (kind) => {
      expect(isNonVisualMediaKind(kind)).toBe(false);
      expect(placeholderFor(kind)).toBeNull();
    },
  );
});

describe('mediaFormatLabel', () => {
  it.each([
    ['audio/mpeg', 'MPEG'],
    ['audio/x-m4a', 'M4A'],
    ['application/pdf', 'PDF'],
    ['image/png; charset=binary', 'PNG'],
  ])('%s badges as %s', (mime, expected) => {
    expect(mediaFormatLabel(mime)).toBe(expected);
  });

  it.each([null, undefined, '', 'notamime', 'audio/'])(
    'returns null rather than an empty badge for %s',
    (mime) => {
      expect(mediaFormatLabel(mime)).toBeNull();
    },
  );

  it('does not truncate — the badge element owns that', () => {
    // It used to hard-slice at 12 characters with no ellipsis, which turned a
    // long Office subtype into a silent stump where the CSS `truncate` would
    // have produced a readable elision. Two truncations is one too many.
    const long = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    expect(mediaFormatLabel(long)).toBe(
      'VND.OPENXMLFORMATS-OFFICEDOCUMENT.WORDPROCESSINGML.DOCUMENT',
    );
  });
});
