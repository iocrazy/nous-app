// features/canvas-core/smart/nodes/OutputNodeView.upscale.test.tsx
//
// Upscale on an output node: a refusal (e.g. a 404 on a generation the caller
// cannot read) must reach the user, not only the console.

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../../../../public/locales/en.json';
import { ApiError } from '../../../../services/apiClient';

// Resolves against the real en.json and interpolates, so the toast text below
// is what a user reads — and a missing locale key fails here. `t` is created
// once so it stays referentially stable, like the real hook's.
vi.mock('react-i18next', () => {
  const t = (
    key: string,
    defaultOrVars?: string | Record<string, unknown>,
    maybeVars?: Record<string, unknown>,
  ): string => {
    const vars = typeof defaultOrVars === 'object' ? defaultOrVars : maybeVars;
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

const addToast = vi.fn();
vi.mock('../../../../components/Toast', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useOptionalToast: () => ({ addToast }),
    useToast: () => ({ addToast }),
  };
});

const upscaleGeneration = vi.fn();
vi.mock('../../services/canvasGenerationService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, upscaleGeneration: (...a: unknown[]) => upscaleGeneration(...a) };
});

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

const SOURCE = '/api/v1/generated-media/727145299382534145/cover';
const ITEM2 = '/api/v1/generated-media/727145299382534222/cover';

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

function renderOutput(overrides: Record<string, unknown> = {}) {
  const data = {
    kind: 'image',
    preview_text: '',
    preview_url: SOURCE,
    crop_region: null,
    images: [{ url: SOURCE, kind: 'image' }],
    ...overrides,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id: 'out1', type: 'output', data, position: { x: 0, y: 0 } }],
  });
  return render(
    <ReactFlowProvider>
      <OutputNodeView {...baseProps} id="out1" type="output" data={data} />
    </ReactFlowProvider>,
  );
}

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({ canvasId: '4242', kind: 'smart', loadStatus: 'ready' });
  addToast.mockReset();
  upscaleGeneration.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('OutputNodeView — upscale', () => {
  it('a refused upscale is reported to the user and logged', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    upscaleGeneration.mockRejectedValue(
      new ApiError('generation not found', 404, { code: 'http_404' }),
    );
    renderOutput();
    fireEvent.click(screen.getByRole('button', { name: 'Upscale' }));

    await waitFor(() => expect(addToast).toHaveBeenCalledTimes(1));
    expect(upscaleGeneration).toHaveBeenCalledWith('727145299382534145', '2k');
    expect(addToast.mock.calls[0][0]).toBe('Upscale failed: generation not found');
    expect(addToast.mock.calls[0][1]).toBe('error');
    expect(consoleError).toHaveBeenCalled();
  });

  it('says nothing when the upscale lands', async () => {
    upscaleGeneration.mockResolvedValue({
      id: '727145299382534999',
      url: '/api/v1/generated-media/727145299382534999/cover',
    });
    renderOutput();
    fireEvent.click(screen.getByRole('button', { name: 'Upscale' }));

    await waitFor(() => {
      const node = useCanvasCoreStore.getState().nodes[0] as {
        data: { images: Array<{ url: string }> };
      };
      expect(node.data.images.map((img) => img.url)).toContain(
        '/api/v1/generated-media/727145299382534999/cover',
      );
    });
    expect(addToast).not.toHaveBeenCalled();
  });

  // Upscale is an edit like crop / expand / split, and every one of those keys
  // on `editSourceUrl` — the picture the user is looking at. Keying on the
  // primary instead upscaled a picture nobody asked about, and the result
  // landed in the grid looking like the answer to the click.
  it('upscales the grid image that was double-clicked, not the primary', async () => {
    upscaleGeneration.mockResolvedValue({
      id: '727145299382534999',
      url: '/api/v1/generated-media/727145299382534999/cover',
    });
    renderOutput({
      images: [
        { url: SOURCE, kind: 'image' },
        { url: ITEM2, kind: 'image' },
      ],
    });
    fireEvent.doubleClick(
      within(screen.getByTestId('output-images-grid')).getAllByRole('img')[1],
    );
    fireEvent.click(screen.getByRole('button', { name: 'Upscale' }));

    await waitFor(() => expect(upscaleGeneration).toHaveBeenCalledTimes(1));
    expect(upscaleGeneration).toHaveBeenCalledWith('727145299382534222', '2k');
  });

  // A library import (`/api/v1/resources/{id}/cover`) has no generated-media
  // row to upscale. The button used to render anyway and `return` on the
  // missing id — a click with no outcome at all, which is the one result that
  // teaches the user nothing.
  it('offers no Upscale when the edited image has no generated-media id', () => {
    renderOutput({
      preview_url: '/api/v1/resources/9/cover',
      images: [{ url: '/api/v1/resources/9/cover', kind: 'image' }],
    });
    expect(screen.queryByRole('button', { name: 'Upscale' })).toBeNull();
    // Negative control: the toolbar itself is there, it is only Upscale that
    // is withheld.
    expect(screen.getByRole('button', { name: 'Brush' })).toBeTruthy();
  });
});
