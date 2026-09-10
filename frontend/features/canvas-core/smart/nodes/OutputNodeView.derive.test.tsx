import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return { ...actual, deriveCanvasCrop: vi.fn(), deriveCanvasGrid: vi.fn() };
});

const { deriveCanvasCrop, deriveCanvasGrid } = await import('../../services/canvasService');

const SOURCE = '/api/v1/generated-media/5/cover';
const DERIVED = {
  id: '901',
  url: '/api/v1/generated-media/901/cover',
  kind: 'image',
  row: null,
  col: null,
};
const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  HTMLElement.prototype.getBoundingClientRect = function fakeRect() {
    return {
      x: 0, y: 0, width: 1000, height: 500, top: 0, left: 0, bottom: 500, right: 1000,
      toJSON: () => ({}),
    } as DOMRect;
  };
  (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockReset();
  (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  type: 'output',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  // Selected: the floating toolbar mounts only while pinned or hovered.
  selected: true,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function seedImageOutput(legacy: Record<string, unknown> = {}) {
  const fullData = {
    kind: 'image',
    preview_text: '',
    preview_url: SOURCE,
    images: [{ url: SOURCE, kind: 'image' }],
    crop_region: null,
    ...legacy,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id: 'o1', type: 'output', data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

function nodeData(): Record<string, unknown> {
  return (useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> }).data;
}

function cropAndApply(fullData: Record<string, unknown>) {
  render(
    <Wrap>
      <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
    </Wrap>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
  fireEvent.click(screen.getByTestId('editor-apply'));
}

describe('OutputNodeView — crop derives from the shown image', () => {
  it('derives by url (no resource_id needed) and swaps the image in place', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockResolvedValueOnce(DERIVED);
    cropAndApply(seedImageOutput());

    await waitFor(() => expect(deriveCanvasCrop).toHaveBeenCalledTimes(1));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      SOURCE,
      expect.objectContaining({ x: 0, y: 0, width: 1, height: 1 }),
      { nodeId: 'o1' },
    );
    await waitFor(() => expect(nodeData().preview_url).toBe(DERIVED.url));
    expect(nodeData().images).toEqual([{ url: DERIVED.url, kind: 'image', id: '901' }]);
    expect(nodeData().crop_region).toBeNull();
    // Nothing session-bearing is persisted into canvas data.
    expect(JSON.stringify(useCanvasCoreStore.getState().nodes)).not.toMatch(/token=/);
    await waitFor(() =>
      expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument(),
    );
  });

  it('a legacy promoted node loses the fields that described the old picture', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockResolvedValueOnce(DERIVED);
    cropAndApply(
      seedImageOutput({
        resource_id: 'source-123',
        crop_region: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 },
      }),
    );

    await waitFor(() => expect(nodeData().preview_url).toBe(DERIVED.url));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      SOURCE,
      expect.objectContaining({ x: 0.1, y: 0.1, width: 0.5, height: 0.5 }),
      { nodeId: 'o1' },
    );
    expect(nodeData().resource_id).toBeNull();
    expect(nodeData().crop_region).toBeNull();
  });

  it('a refused derive shows the server message and changes nothing', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('source image not found'),
    );
    cropAndApply(seedImageOutput());

    await waitFor(() => expect(deriveCanvasCrop).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('crop-commit-error');
    expect(banner.textContent).toMatch(/source image not found/);
    // The node's own banner sits beneath the editor's body-portalled overlay;
    // the reason must also render INSIDE the dialog, where a user can see it.
    const inEditor = within(screen.getByTestId('unified-image-editor')).getByTestId(
      'editor-commit-error',
    );
    expect(inEditor.textContent).toMatch(/source image not found/);
    expect(nodeData().preview_url).toBe(SOURCE);
  });
});

function editorError(): HTMLElement | null {
  return within(screen.getByTestId('unified-image-editor')).queryByTestId('editor-commit-error');
}

describe('OutputNodeView — the dialog shows the CURRENT refusal only', () => {
  it('a split refusal is forgotten once the editor closes', async () => {
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('at least one split line is required'),
    );
    const fullData = seedImageOutput();
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Split' }));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));
    await waitFor(() => expect(editorError()?.textContent).toMatch(/split line/));

    fireEvent.click(screen.getByTestId('editor-cancel'));
    await waitFor(() =>
      expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    expect(editorError()).toBeNull();
  });

  it('a later split refusal replaces an earlier crop refusal', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('source image not found'),
    );
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('at least one split line is required'),
    );
    cropAndApply(seedImageOutput());
    await waitFor(() => expect(editorError()?.textContent).toMatch(/source image not found/));

    fireEvent.click(screen.getByTestId('editor-tab-split'));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(deriveCanvasGrid).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(editorError()?.textContent).toMatch(/split line/));
    expect(editorError()?.textContent).not.toMatch(/source image not found/);
  });
});

describe('OutputNodeView — a crop never drops images that land during the derive', () => {
  it('keeps a generation result appended while the crop was in flight', async () => {
    let resolveDerive: (value: typeof DERIVED) => void = () => {};
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise<typeof DERIVED>((resolve) => {
        resolveDerive = resolve;
      }),
    );
    cropAndApply(seedImageOutput());
    await waitFor(() => expect(deriveCanvasCrop).toHaveBeenCalledTimes(1));

    // A generation result lands in the same node while the round trip runs.
    const LANDED = { url: '/api/v1/generated-media/77/cover', kind: 'image' };
    useCanvasCoreStore.getState().patchNode('o1', {
      data: { images: [...(nodeData().images as unknown[]), LANDED] },
    });
    resolveDerive(DERIVED);

    await waitFor(() => expect(nodeData().preview_url).toBe(DERIVED.url));
    expect(nodeData().images).toEqual([{ url: DERIVED.url, kind: 'image', id: '901' }, LANDED]);
  });
});
