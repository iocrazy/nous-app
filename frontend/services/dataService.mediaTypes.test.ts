import { describe, it, expect } from 'vitest';
import { mediaTypesToWire } from './dataService';

describe('mediaTypesToWire', () => {
  it('returns null when no type filter is active', () => {
    expect(mediaTypesToWire(undefined)).toBeNull();
    expect(mediaTypesToWire([])).toBeNull();
  });

  it('maps a single group to its wire values', () => {
    expect(mediaTypesToWire(['video'])).toEqual([
      'video', 'special', 'short', 'live_clip', '0', '4', '61',
    ]);
    expect(mediaTypesToWire(['audio'])).toEqual(['audio']);
  });

  it('flattens multiple groups in order', () => {
    expect(mediaTypesToWire(['image', 'audio'])).toEqual([
      'carousel', 'image_text', '2', '68', 'audio',
    ]);
  });

  it('forces zero rows for a group with no wire values (document)', () => {
    expect(mediaTypesToWire(['document'])).toEqual(['__impossible__']);
    expect(mediaTypesToWire(['other'])).toEqual(['__impossible__']);
  });

  it('keeps real values when an empty group is combined with a real one', () => {
    // document contributes nothing but video still matches → not impossible.
    expect(mediaTypesToWire(['document', 'video'])).toEqual([
      'video', 'special', 'short', 'live_clip', '0', '4', '61',
    ]);
  });

  it('ignores unknown group keys', () => {
    expect(mediaTypesToWire(['nonexistent'])).toEqual(['__impossible__']);
  });
});
