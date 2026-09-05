// features/canvas-core/smart/nodes/OutputNodeView.asAsset.test.tsx
//
// "As Asset" on an output node (P4 Task 6): promote a generated picture into
// the asset library.
//
// The `GeneratedItem` fixture is the wire shape `GET /generated/{id}` really
// answers — the same one the list returns, string ids and all — because that
// is what `SaveAsAssetDialog` consumes. A prettier hand-written shape here
// would let a mismatch reach production untested (CLAUDE.md, 边界 mock).

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SCOPE = '727145299382534100';
const GEN_ID = '727145299382534145';

const GENERATED_ITEM = {
  id: GEN_ID,
  scope_id: SCOPE,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'A dockworker at dawn. more',
  model: 'gpt-image-2',
  provider: 'openai',
  origin_kind: 'canvas_run',
  canvas_id: '325005725244722',
  node_id: 'out1',
  created_at: '2026-09-02T10:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed' as const,
  source_asset_id: '727145299382534201',
  source: {
    kind: 'canvas_run',
    label: 'EP1 · Storyboard · Canvas',
    canvas_id: '325005725244722',
    node_id: 'out1',
    shot_id: null,
    conversation_id: null,
    deep_link: '/team/727145299382534100/canvas/325005725244722?node=out1',
  },
  title: 'A dockworker at dawn',
};

// The toast is MOCKED rather than left absent. Without a provider,
// `useOptionalToast()` answers null and `toast?.addToast(...)` is a no-op — so
// the failure case below asserted only that no dialog opened, which is also
// exactly what a regression that dropped the user-visible report looks like.
// The `console.error` half was covered; the half the user sees was not.
const addToast = vi.fn();
vi.mock('../../../../components/Toast', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useOptionalToast: () => ({ addToast }),
    useToast: () => ({ addToast }),
  };
});

const fetchGeneratedItem = vi.fn();
vi.mock('../../../../services/generatedService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, fetchGeneratedItem: (...a: unknown[]) => fetchGeneratedItem(...a) };
});

// The dialog itself is P1's and has its own suite; here it stands in as a
// marker so the assertions are about WHAT IT WAS HANDED, not its internals.
const dialogProps = vi.fn();
vi.mock('../../../../components/assets/SaveAsAssetDialog', () => ({
  SaveAsAssetDialog: (props: Record<string, unknown>) => {
    dialogProps(props);
    return <div data-testid="save-as-asset-dialog" />;
  },
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

const baseProps = {
  selected: true,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

function renderOutput(
  data: Record<string, unknown>,
  path = `/team/${SCOPE}/canvas/9`,
) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={
            <ReactFlowProvider>
              <OutputNodeView {...baseProps} id="out1" type="output" data={data} />
            </ReactFlowProvider>
          }
        />
        <Route
          path="/canvas/:canvasId"
          element={
            <ReactFlowProvider>
              <OutputNodeView {...baseProps} id="out1" type="output" data={data} />
            </ReactFlowProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const imageOutput = (over: Record<string, unknown> = {}) => ({
  kind: 'image',
  resource_id: null,
  preview_text: '',
  preview_url: '/api/v1/generated-media/727145299382534145/cover',
  crop_region: null,
  images: [
    {
      url: '/api/v1/generated-media/727145299382534145/cover',
      kind: 'image',
      id: GEN_ID,
    },
  ],
  ...over,
});

const asAsset = () => screen.getByRole('button', { name: 'As Asset' });

beforeEach(() => {
  fetchGeneratedItem.mockReset().mockResolvedValue(GENERATED_ITEM);
  addToast.mockReset();
  dialogProps.mockReset();
  useCanvasCoreStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('As Asset', () => {
  it('reads the generation row and hands the dialog that ONE item', async () => {
    renderOutput(imageOutput());
    fireEvent.click(asAsset());
    await waitFor(() => expect(screen.getByTestId('save-as-asset-dialog')).toBeTruthy());

    expect(fetchGeneratedItem).toHaveBeenCalledWith(SCOPE, GEN_ID);
    const props = dialogProps.mock.calls.at(-1)![0] as Record<string, unknown>;
    expect(props.open).toBe(true);
    expect(props.scopeId).toBe(SCOPE);
    expect(props.items).toEqual([GENERATED_ITEM]);
    // The prefill this whole hop exists for: the row carries the asset the run
    // came from, and the canvas node never did.
    expect((props.items as typeof GENERATED_ITEM[])[0].source_asset_id).toBe(
      '727145299382534201',
    );
  });

  it('recovers the id from the durable url when the ref carries none', async () => {
    // Nodes persisted before `GeneratedImageRef.id` existed still name the row
    // in their url — the same recovery `handleUpscale` already does.
    renderOutput(
      imageOutput({
        images: [
          { url: '/api/v1/generated-media/727145299382534145/cover', kind: 'image' },
        ],
      }),
    );
    fireEvent.click(asAsset());
    await waitFor(() => expect(fetchGeneratedItem).toHaveBeenCalledWith(SCOPE, GEN_ID));
  });

  it('is DISABLED with a reason when no row can be named — never silent', async () => {
    renderOutput(
      imageOutput({
        preview_url: 'https://example.test/pasted.png',
        images: [{ url: 'https://example.test/pasted.png', kind: 'image' }],
      }),
    );
    const button = asAsset();
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      'title',
      'This image has no library record to save',
    );
    fireEvent.click(button);
    expect(fetchGeneratedItem).not.toHaveBeenCalled();
  });

  it('is DISABLED with its own reason outside a team route', async () => {
    // `/api/v1/assets` is scoped per request; an empty `scope_id` is a 403,
    // not an unscoped query, so the key says which is missing.
    renderOutput(imageOutput(), '/canvas/9');
    const button = asAsset();
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      'title',
      'Open this canvas from a workspace to save assets',
    );
  });

  it('opens nothing when the row cannot be read, and SAYS so', async () => {
    // Both halves. "No dialog opened" alone is indistinguishable from a
    // regression that silently swallowed the failure — the user clicked, and
    // "nothing happened" is the one outcome that teaches them the button is
    // broken.
    fetchGeneratedItem.mockRejectedValue(new Error('404'));
    renderOutput(imageOutput());
    fireEvent.click(asAsset());
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(screen.queryByTestId('save-as-asset-dialog')).toBeNull();
    expect(addToast.mock.calls[0][1]).toBe('error');
  });

  it('says nothing on the happy path', async () => {
    // Negative control: a toast on every click is a toast nobody reads, and it
    // would make the assertion above pass for the wrong reason.
    renderOutput(imageOutput());
    fireEvent.click(asAsset());
    await waitFor(() => expect(screen.getByTestId('save-as-asset-dialog')).toBeTruthy());
    expect(addToast).not.toHaveBeenCalled();
  });

  it('leaves the node itself untouched — promoting is not a canvas edit', async () => {
    useCanvasCoreStore.setState({
      canvasId: 'c1',
      kind: 'smart',
      loadStatus: 'ready',
      nodes: [{ id: 'out1', type: 'output', position: { x: 0, y: 0 }, data: {} }],
    } as never);
    const before = useCanvasCoreStore.getState().nodes;
    renderOutput(imageOutput());
    fireEvent.click(asAsset());
    await waitFor(() => expect(screen.getByTestId('save-as-asset-dialog')).toBeTruthy());
    expect(useCanvasCoreStore.getState().nodes).toBe(before);
  });
});
