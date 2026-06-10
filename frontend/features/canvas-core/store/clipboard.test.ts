import { afterEach, describe, expect, it } from 'vitest';

import {
  PASTE_OFFSET,
  clearClipboard,
  copyNodesToClipboard,
  preparePastedNodes,
  readClipboard,
} from './clipboard';

afterEach(() => clearClipboard());

describe('clipboard module', () => {
  it('readClipboard returns null when nothing was copied', () => {
    expect(readClipboard()).toBeNull();
  });

  it('roundtrips nodes through copy → read', () => {
    copyNodesToClipboard([{ id: 'a' }, { id: 'b' }]);
    const got = readClipboard();
    expect(got?.nodes).toEqual([{ id: 'a' }, { id: 'b' }]);
  });

  it('copy is a deep clone — mutating returned nodes does not affect later reads', () => {
    copyNodesToClipboard([{ id: 'a', data: { label: 'one' } }]);
    const first = readClipboard()!;
    (first.nodes[0] as Record<string, unknown>).data = { label: 'mutated' };
    const second = readClipboard()!;
    expect((second.nodes[0] as Record<string, unknown>).data).toEqual({
      label: 'one',
    });
  });

  it('clearClipboard wipes the payload', () => {
    copyNodesToClipboard([{ id: 'a' }]);
    clearClipboard();
    expect(readClipboard()).toBeNull();
  });
});

describe('preparePastedNodes', () => {
  it('re-ids nodes whose id collides with existing', () => {
    const pasted = preparePastedNodes(
      [{ id: 'a' }, { id: 'b' }],
      new Set(['a']),
    );
    expect(pasted[0]).toMatchObject({ id: 'a-2' });
    expect(pasted[1]).toMatchObject({ id: 'b' });
  });

  it('does not collide repeatedly on a third paste', () => {
    const existing = new Set(['a', 'a-2']);
    const pasted = preparePastedNodes([{ id: 'a' }], existing);
    expect(pasted[0]).toMatchObject({ id: 'a-3' });
  });

  it('offsets position by PASTE_OFFSET', () => {
    const pasted = preparePastedNodes(
      [{ id: 'a', position: { x: 100, y: 50 } }],
      new Set(),
    );
    expect((pasted[0] as Record<string, unknown>).position).toEqual({
      x: 100 + PASTE_OFFSET,
      y: 50 + PASTE_OFFSET,
    });
  });

  it('falls back to PASTE_OFFSET when position is missing', () => {
    const pasted = preparePastedNodes(
      [{ id: 'a' }],
      new Set(),
    );
    expect((pasted[0] as Record<string, unknown>).position).toEqual({
      x: PASTE_OFFSET,
      y: PASTE_OFFSET,
    });
  });

  it('synthesises a random id when source lacks one', () => {
    const pasted = preparePastedNodes(
      [{ position: { x: 0, y: 0 } }],
      new Set(),
    );
    expect(typeof (pasted[0] as Record<string, unknown>).id).toBe('string');
  });
});
