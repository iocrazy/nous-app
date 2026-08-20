// IC reorderManualInputRefs: dragging a manual reference thumb reorders
// manual_refs — order is functional (it decides image_1/image_2… roles).
import { describe, expect, it } from 'vitest';

import { reorderRefs } from './refOrder';

const refs = [
  { url: '/a', kind: 'image' },
  { url: '/b', kind: 'image' },
  { url: '/c', kind: 'image' },
];

describe('reorderRefs', () => {
  it('moves an item before another', () => {
    expect(reorderRefs(refs, '/c', '/a', true).map((r) => r.url)).toEqual([
      '/c', '/a', '/b',
    ]);
  });

  it('moves an item after another', () => {
    expect(reorderRefs(refs, '/a', '/c', false).map((r) => r.url)).toEqual([
      '/b', '/c', '/a',
    ]);
  });

  it('unknown urls leave the list untouched', () => {
    expect(reorderRefs(refs, '/x', '/a', true)).toEqual(refs);
    expect(reorderRefs(refs, '/a', '/x', true)).toEqual(refs);
  });
});
