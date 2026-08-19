/**
 * MediaNodeView — IC image-card parity (A): selected card shows the
 * floating toolbar (Preview/Download — edit keys need the resources bridge
 * and are deliberately absent), each cell hover-reveals a delete key, and
 * loaded images carry a W×H resolution badge.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { MediaNodeView } from './MediaNodeView';

const URL_A = '/api/v1/generated-media/1/file';
const URL_B = '/api/v1/generated-media/2/file';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

function seed(items: Array<{ url: string; kind: string; name?: string }>): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '1',
    nodes: [
      {
        id: 'm1',
        type: 'media',
        position: { x: 0, y: 0 },
        data: { title: 'Media', items },
      } as unknown as CanvasNode,
    ],
    connections: [],
    selection: [],
  });
}

function itemsOf(): Array<{ url: string }> {
  return (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'm1') as {
    data: { items: Array<{ url: string }> };
  }).data.items;
}

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

function renderNode(selected = true): void {
  const data = (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'm1') as { data: unknown }).data;
  render(
    <ReactFlowProvider>
      <MediaNodeView
        {...baseProps}
        selected={selected}
        id="m1"
        type="media"
        data={data as never}
      />
    </ReactFlowProvider>,
  );
}

describe('MediaNodeView IC card parity', () => {
  it('selected card shows the floating toolbar with Preview and Download', () => {
    seed([{ url: URL_A, kind: 'image', name: 'a.png' }]);
    renderNode();
    expect(screen.getByTestId('output-node-toolbar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('per-cell delete removes just that item', () => {
    seed([
      { url: URL_A, kind: 'image', name: 'a.png' },
      { url: URL_B, kind: 'image', name: 'b.png' },
    ]);
    renderNode();
    const dels = screen.getAllByTestId('media-item-delete');
    expect(dels).toHaveLength(2);
    fireEvent.click(dels[0]);
    expect(itemsOf().map((i) => i.url)).toEqual([URL_B]);
  });

  it('read-only hides delete keys', () => {
    seed([{ url: URL_A, kind: 'image', name: 'a.png' }]);
    useCanvasCoreStore.setState({ readOnly: true });
    renderNode();
    expect(screen.queryByTestId('media-item-delete')).toBeNull();
  });

  it('image cells carry a resolution badge slot that fills on load', () => {
    seed([{ url: URL_A, kind: 'image', name: 'a.png' }]);
    renderNode();
    const img = screen
      .getByTestId('media-node-thumb-0')
      .querySelector('img') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 1024 });
    Object.defineProperty(img, 'naturalHeight', { value: 768 });
    fireEvent.load(img);
    expect(screen.getByTestId('media-res-badge').textContent).toBe('1024 x 768');
  });
});
