// features/canvas-core/smart/dropCreate.test.ts
// IC-parity file drop onto blank canvas: a single media node is created at
// the drop point and every dropped file uploads into it; two or more images
// auto-promote the node to its "Group" grid form (title rule copied from
// IC's observed behavior — Group/Videos/Media/Image/Video). Paste with a
// media node selected appends instead of creating.

import { afterEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { MediaNodeData } from './types';
import {
  CANVAS_DROP_MAX,
  createMediaNodeFromFiles,
  pasteFilesToCanvas,
  titleForDroppedFiles,
} from './dropCreate';

vi.mock('./mediaImport', async (importOriginal) => {
  const original = await importOriginal<typeof import('./mediaImport')>();
  return {
    ...original,
    importCanvasMedia: vi.fn(async (file: File) => ({
      url: `https://x/generated-media/${file.name}`,
      name: file.name,
      kind: file.type.startsWith('video/') ? 'video' : 'image',
    })),
  };
});

function img(name: string): File {
  return new File([new Uint8Array([1])], name, { type: 'image/png' });
}
function vid(name: string): File {
  return new File([new Uint8Array([1])], name, { type: 'video/mp4' });
}

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '42',
    nodes: [],
    connections: [],
    selection: [],
  });
}

function mediaNodes() {
  return useCanvasCoreStore
    .getState()
    .nodes.filter((n) => n.type === 'media') as Array<{
    id: string;
    position: { x: number; y: number };
    data: MediaNodeData;
  }>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('titleForDroppedFiles (IC naming rule)', () => {
  it.each([
    [[img('a.png'), img('b.png')], 'Group'],
    [[vid('a.mp4'), vid('b.mp4')], 'Videos'],
    [[img('a.png'), vid('b.mp4')], 'Media'],
    [[img('a.png')], 'Image'],
    [[vid('a.mp4')], 'Video'],
  ])('names %# correctly', (files, expected) => {
    expect(titleForDroppedFiles(files as File[])).toBe(expected);
  });
});

describe('createMediaNodeFromFiles', () => {
  it('creates one media node at the drop point and uploads every file into it', async () => {
    seed();
    await createMediaNodeFromFiles([img('a.png'), img('b.png')], {
      x: 500,
      y: 300,
    });

    const nodes = mediaNodes();
    expect(nodes).toHaveLength(1);
    const node = nodes[0];
    // Centered on the drop point: x is offset left by half the node width.
    expect(node.position.x).toBeLessThan(500);
    expect(node.position.y).toBeLessThan(300);
    expect(node.data.items?.map((i) => i.name)).toEqual(['a.png', 'b.png']);
    expect(node.data.uploading ?? 0).toBe(0);
    expect(node.data.title).toBe('Group');
  });

  it('keeps the single-image title rule', async () => {
    seed();
    await createMediaNodeFromFiles([img('solo.png')], { x: 0, y: 0 });
    expect(mediaNodes()[0].data.title).toBe('Image');
  });

  it('filters non-importable files and creates nothing when none remain', async () => {
    seed();
    const junk = new File([new Uint8Array([1])], 'a.txt', {
      type: 'text/plain',
    });
    await createMediaNodeFromFiles([junk], { x: 0, y: 0 });
    expect(mediaNodes()).toHaveLength(0);
  });

  it('caps a single drop at CANVAS_DROP_MAX files', async () => {
    seed();
    const files = Array.from({ length: CANVAS_DROP_MAX + 5 }, (_, i) =>
      img(`f${i}.png`),
    );
    await createMediaNodeFromFiles(files, { x: 0, y: 0 });
    expect(mediaNodes()[0].data.items).toHaveLength(CANVAS_DROP_MAX);
  });

  it('survives a mid-batch upload failure and still lands the rest', async () => {
    seed();
    const { importCanvasMedia } = await import('./mediaImport');
    (importCanvasMedia as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error('boom'));
    await createMediaNodeFromFiles([img('bad.png'), img('ok.png')], {
      x: 0,
      y: 0,
    });
    const node = mediaNodes()[0];
    expect(node.data.items?.map((i) => i.name)).toEqual(['ok.png']);
    expect(node.data.uploading ?? 0).toBe(0);
  });
});

describe('pasteFilesToCanvas', () => {
  it('appends to the selected media node instead of creating a new one', async () => {
    seed();
    useCanvasCoreStore.setState({
      nodes: [
        {
          id: 'media1',
          type: 'media',
          position: { x: 10, y: 10 },
          selected: true,
          data: { title: 'Media', items: [] },
        },
      ],
    });
    await pasteFilesToCanvas([img('p.png')], { x: 100, y: 100 });
    const nodes = mediaNodes();
    expect(nodes).toHaveLength(1);
    expect(nodes[0].id).toBe('media1');
    expect(nodes[0].data.items?.map((i) => i.name)).toEqual(['p.png']);
  });

  it('creates a new node at the given center when nothing relevant is selected', async () => {
    seed();
    await pasteFilesToCanvas([img('p.png')], { x: 100, y: 100 });
    expect(mediaNodes()).toHaveLength(1);
  });
});
