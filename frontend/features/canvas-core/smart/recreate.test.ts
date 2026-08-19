// features/canvas-core/smart/recreate.test.ts
// IC-parity ⑤ (点击节点出现再创作面板): createPromptFromNode spawns a
// prompt node below the source, wires source → prompt, and selects the
// prompt — the prompt node IS the composer (body/@/gen settings/Run), so
// this one action reproduces Infinite's attached panel.

import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { createPromptFromNode } from './recreate';

const MEDIA: CanvasNode = {
  id: 'm1',
  type: 'media',
  position: { x: 100, y: 100 },
  measured: { width: 240, height: 200 },
  data: {
    title: 'Media',
    items: [{ url: '/api/v1/generated-media/a.png', kind: 'image' }],
  },
} as unknown as CanvasNode;

function seed(nodes: CanvasNode[]): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '42',
    nodes,
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('createPromptFromNode', () => {
  it('spawns an image-gen prompt below the node, wired and selected', () => {
    seed([MEDIA]);
    const promptId = createPromptFromNode('m1');
    const s = useCanvasCoreStore.getState();
    const prompt = s.nodes.find((n) => (n as { id: string }).id === promptId) as {
      type: string;
      position: { x: number; y: number };
      data: { gen?: { kind: string } | null };
    };
    expect(prompt.type).toBe('prompt');
    // Below the source node (IC's attached panel position).
    expect(prompt.position.y).toBeGreaterThan(100 + 200);
    // Image generation preset — the media card's re-creation is i2i.
    expect(prompt.data.gen?.kind).toBe('image');
    // Wired source → prompt so the input-image machinery sees it.
    expect(
      s.connections.some(
        (c) => String(c.source) === 'm1' && String(c.target) === promptId,
      ),
    ).toBe(true);
    expect(s.selection).toEqual([promptId]);
  });

  it('unknown node → null, store untouched', () => {
    seed([MEDIA]);
    expect(createPromptFromNode('nope')).toBeNull();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});


describe('createPromptFromNode with init', () => {
  it('seeds body / gen / source_ref onto the spawned prompt', () => {
    seed([MEDIA]);
    const promptId = createPromptFromNode('m1', {
      body: 'a red apple',
      gen: { kind: 'video', model: 'jimeng-cli-seedance', aspect: '16:9' },
      source_ref: '/api/v1/generated-media/a.png',
    });
    const prompt = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { id: string }).id === promptId) as {
      data: {
        body: string;
        gen?: { kind: string; model: string };
        source_ref?: string;
      };
    };
    expect(prompt.data.body).toBe('a red apple');
    expect(prompt.data.gen?.kind).toBe('video');
    expect(prompt.data.gen?.model).toBe('jimeng-cli-seedance');
    expect(prompt.data.source_ref).toBe('/api/v1/generated-media/a.png');
  });
});
