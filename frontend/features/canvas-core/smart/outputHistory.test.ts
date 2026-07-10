// features/canvas-core/smart/outputHistory.test.ts
// Replace-with-history semantics (Infinite parity G4-F2): new results REPLACE
// an output node's images; whatever was there first gets archived into a
// dedicated history node (data.history_for), created once below the slot and
// accumulating newest-first — Infinite's replaceOutputsToNodeWithHistory +
// 历史分组, minus the group node type smart mode doesn't have.

import { afterEach, describe, expect, it } from 'vitest';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { replaceOutputImagesWithHistory } from './outputHistory';
import type { CanvasNode } from '../types';

function outputNode(data: Record<string, unknown>): CanvasNode {
  return { id: 'out1', type: 'output', position: { x: 400, y: 100 }, data };
}

function seed(data: Record<string, unknown>): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [outputNode(data)],
    connections: [],
    selection: [],
  });
}

function state() {
  return useCanvasCoreStore.getState();
}

function nodeById(id: string): Record<string, unknown> | undefined {
  return state().nodes.find((n) => (n as CanvasNode).id === id) as
    | Record<string, unknown>
    | undefined;
}

function historyNodes(): Array<Record<string, unknown>> {
  return state().nodes.filter(
    (n) => ((n as Record<string, unknown>).data as { history_for?: string })?.history_for,
  ) as Array<Record<string, unknown>>;
}

afterEach(() => useCanvasCoreStore.getState().reset());

describe('replaceOutputImagesWithHistory', () => {
  it('first fill sets images + preview compat fields, no history node', () => {
    seed({ kind: 'text', resource_id: null, preview_text: '', preview_url: null, crop_region: null });
    replaceOutputImagesWithHistory('out1', ['/gm/1/cover', '/gm/2/cover'], 'image');

    const data = nodeById('out1')!.data as Record<string, unknown>;
    expect(data.images).toEqual([
      { url: '/gm/1/cover', kind: 'image' },
      { url: '/gm/2/cover', kind: 'image' },
    ]);
    expect(data.kind).toBe('image');
    expect(data.preview_url).toBe('/gm/1/cover');
    expect(historyNodes()).toHaveLength(0);
  });

  it('re-fill archives the previous images into one history node', () => {
    seed({ kind: 'text', resource_id: null, preview_text: '', preview_url: null, crop_region: null });
    replaceOutputImagesWithHistory('out1', ['/gm/1/cover'], 'image');
    replaceOutputImagesWithHistory('out1', ['/gm/2/cover'], 'image');

    const data = nodeById('out1')!.data as Record<string, unknown>;
    expect(data.images).toEqual([{ url: '/gm/2/cover', kind: 'image' }]);

    const history = historyNodes();
    expect(history).toHaveLength(1);
    const hData = history[0].data as { images: Array<{ url: string }>; history_for: string };
    expect(hData.history_for).toBe('out1');
    expect(hData.images).toEqual([{ url: '/gm/1/cover', kind: 'image' }]);
    // Linked from the slot so the provenance is visible on the canvas.
    const conns = state().connections as Array<Record<string, unknown>>;
    expect(conns.some((c) => c.source === 'out1' && c.target === history[0].id)).toBe(true);
  });

  it('third fill accumulates into the SAME history node, newest-first', () => {
    seed({ kind: 'text', resource_id: null, preview_text: '', preview_url: null, crop_region: null });
    replaceOutputImagesWithHistory('out1', ['/gm/1/cover'], 'image');
    replaceOutputImagesWithHistory('out1', ['/gm/2/cover'], 'image');
    replaceOutputImagesWithHistory('out1', ['/gm/3/cover'], 'image');

    const history = historyNodes();
    expect(history).toHaveLength(1);
    const hImages = (history[0].data as { images: Array<{ url: string }> }).images;
    expect(hImages.map((i) => i.url)).toEqual(['/gm/2/cover', '/gm/1/cover']);
  });

  it('archives a legacy preview_url-only node as one image', () => {
    seed({ kind: 'image', resource_id: null, preview_text: '', preview_url: '/old.png', crop_region: null });
    replaceOutputImagesWithHistory('out1', ['/gm/9/cover'], 'image');

    const history = historyNodes();
    expect(history).toHaveLength(1);
    expect((history[0].data as { images: Array<{ url: string }> }).images).toEqual([
      { url: '/old.png', kind: 'image' },
    ]);
  });

  it('never touches undo history', () => {
    seed({ kind: 'text', resource_id: null, preview_text: '', preview_url: null, crop_region: null });
    replaceOutputImagesWithHistory('out1', ['/gm/1/cover'], 'image');
    replaceOutputImagesWithHistory('out1', ['/gm/2/cover'], 'image');
    expect(state().canUndo()).toBe(false);
  });
});

describe('latestHistoryImageUrl', () => {
  it('returns the newest archived image for a slot (G7 compare source)', async () => {
    const { latestHistoryImageUrl } = await import('./outputHistory');
    seed({ kind: 'image', preview_url: '/gm/1/cover', images: [{ url: '/gm/1/cover', kind: 'image' }] });
    replaceOutputImagesWithHistory('out1', ['/gm/2/cover'], 'image');
    // /gm/1 was archived newest-first — it is the compare source now.
    expect(latestHistoryImageUrl('out1')).toBe('/gm/1/cover');
  });

  it('returns null when the slot has no history node', async () => {
    const { latestHistoryImageUrl } = await import('./outputHistory');
    seed({ kind: 'image', preview_url: '/gm/1/cover' });
    expect(latestHistoryImageUrl('out1')).toBeNull();
  });
});
