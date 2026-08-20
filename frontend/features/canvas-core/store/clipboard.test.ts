import { afterEach, describe, expect, it } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import {
  PASTE_OFFSET,
  clearClipboard,
  copyToClipboard,
  preparePaste,
  readClipboard,
} from './clipboard';

const node = (id: string, extra: Record<string, unknown> = {}): CanvasNode =>
  ({ id, ...extra }) as CanvasNode;
const edge = (
  id: string,
  source: string,
  target: string,
): CanvasConnection => ({ id, source, target }) as CanvasConnection;

afterEach(() => clearClipboard());

describe('clipboard module', () => {
  it('readClipboard returns null when nothing was copied', () => {
    expect(readClipboard()).toBeNull();
  });

  it('roundtrips nodes + kind through copy → read', () => {
    copyToClipboard('smart', [node('a'), node('b')], []);
    const got = readClipboard();
    expect(got?.kind).toBe('smart');
    expect(got?.nodes).toEqual([node('a'), node('b')]);
  });

  it('copy captures only edges internal to the selection', () => {
    copyToClipboard(
      'character',
      [node('a'), node('b')],
      [edge('e1', 'a', 'b'), edge('e2', 'a', 'outside')],
    );
    const got = readClipboard()!;
    expect(got.connections).toEqual([edge('e1', 'a', 'b')]);
  });

  it('copy is a deep clone — mutating a read does not affect later reads', () => {
    copyToClipboard('smart', [node('a', { data: { label: 'one' } })], []);
    const first = readClipboard()!;
    (first.nodes[0] as Record<string, unknown>).data = { label: 'mutated' };
    const second = readClipboard()!;
    expect((second.nodes[0] as Record<string, unknown>).data).toEqual({
      label: 'one',
    });
  });

  it('clearClipboard wipes the payload', () => {
    copyToClipboard('smart', [node('a')], []);
    clearClipboard();
    expect(readClipboard()).toBeNull();
  });
});

describe('preparePaste', () => {
  it('returns null when the clipboard is empty', () => {
    expect(preparePaste(new Set())).toBeNull();
  });

  it('re-ids nodes whose id collides with existing', () => {
    copyToClipboard('smart', [node('a'), node('b')], []);
    const out = preparePaste(new Set(['a']))!;
    expect(out.nodes[0]).toMatchObject({ id: 'a-2' });
    expect(out.nodes[1]).toMatchObject({ id: 'b' });
  });

  it('remaps internal edges onto the new node ids (no orphaned paste)', () => {
    copyToClipboard('smart', [node('a'), node('b')], [edge('e1', 'a', 'b')]);
    const out = preparePaste(new Set(['a', 'b']))!;
    const newA = (out.nodes[0] as Record<string, unknown>).id as string;
    const newB = (out.nodes[1] as Record<string, unknown>).id as string;
    expect(out.connections).toHaveLength(1);
    expect(out.connections[0]).toMatchObject({ source: newA, target: newB });
    expect(out.connections[0].id).not.toBe('e1'); // fresh edge id
  });

  it('offsets position by PASTE_OFFSET on the first paste', () => {
    copyToClipboard('smart', [node('a', { position: { x: 100, y: 50 } })], []);
    const out = preparePaste(new Set())!;
    expect((out.nodes[0] as Record<string, unknown>).position).toEqual({
      x: 100 + PASTE_OFFSET,
      y: 50 + PASTE_OFFSET,
    });
  });

  it('cascades the offset outward on repeated pastes', () => {
    copyToClipboard('smart', [node('a', { position: { x: 0, y: 0 } })], []);
    const first = preparePaste(new Set())!;
    const second = preparePaste(new Set())!;
    expect((first.nodes[0] as Record<string, unknown>).position).toEqual({
      x: PASTE_OFFSET,
      y: PASTE_OFFSET,
    });
    expect((second.nodes[0] as Record<string, unknown>).position).toEqual({
      x: PASTE_OFFSET * 2,
      y: PASTE_OFFSET * 2,
    });
  });

  it('synthesises a random id when the source node lacks one', () => {
    const anon = { position: { x: 0, y: 0 } } as unknown as CanvasNode;
    copyToClipboard('smart', [anon], []);
    const out = preparePaste(new Set())!;
    expect(typeof (out.nodes[0] as Record<string, unknown>).id).toBe('string');
  });
});

describe('smart data-tag hygiene on clone (G6)', () => {
  it('preparePaste remaps gen_slot to the pasted prompt id', () => {
    const prompt = { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'x' } };
    const slot = {
      id: 'out1',
      type: 'output',
      position: { x: 320, y: 0 },
      data: { kind: 'image', gen_slot: { node_id: 'p1', index: 0 } },
    };
    copyToClipboard('smart', [prompt, slot] as never, []);
    const prepared = preparePaste(new Set(['p1', 'out1']))!;
    const pastedPrompt = prepared.nodes.find(
      (n) => (n as { type?: string }).type === 'prompt',
    ) as { id: string };
    const pastedSlot = prepared.nodes.find(
      (n) => (n as { type?: string }).type === 'output',
    ) as { data: { gen_slot: { node_id: string } } };
    expect(pastedSlot.data.gen_slot.node_id).toBe(pastedPrompt.id);
    expect(pastedPrompt.id).not.toBe('p1');
  });
});

describe('parentId hygiene on clone (②-3 groups)', () => {
  it('remaps parentId when the group came along, strips it otherwise', () => {
    const group = { id: 'g1', type: 'group', position: { x: 0, y: 0 }, style: { width: 200, height: 100 }, data: {} };
    const child = { id: 'a', type: 'shot', position: { x: 24, y: 24 }, parentId: 'g1', data: {} };
    copyToClipboard('smart', [group, child] as never, []);
    const withGroup = preparePaste(new Set(['g1', 'a']))!;
    const pastedChild = withGroup.nodes.find((n) => (n as { type?: string }).type === 'shot') as Record<string, unknown>;
    const pastedGroup = withGroup.nodes.find((n) => (n as { type?: string }).type === 'group') as Record<string, unknown>;
    expect(pastedChild.parentId).toBe(pastedGroup.id);

    copyToClipboard('smart', [child] as never, []);
    const alone = preparePaste(new Set(['a']))!;
    const orphan = alone.nodes[0] as Record<string, unknown>;
    expect(orphan.parentId).toBeUndefined();
  });
});

describe('preparePaste at a pointer position (IC lastMouseWorld)', () => {
  afterEach(() => clearClipboard());

  it('centres the pasted subgraph bounding box on the given point', () => {
    copyToClipboard(
      'smart',
      [
        { id: 'a', type: 'prompt', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'output', position: { x: 100, y: 200 }, data: {} },
      ] as never,
      [] as never,
    );
    const prepared = preparePaste(new Set(['a', 'b']), { at: { x: 500, y: 500 } });
    // bbox centre of originals is (50, 100) → shift +450/+400.
    const pos = prepared!.nodes.map((n) => (n as { position: { x: number; y: number } }).position);
    expect(pos[0]).toEqual({ x: 450, y: 400 });
    expect(pos[1]).toEqual({ x: 550, y: 600 });
  });
});
