// features/canvas-core/smart/nodes/fullResTier.test.tsx
// Canvas fluency W1/W2 — the two halves of the preview tier, pinned side by
// side because they only make sense against each other:
//
//   • Nodes on the canvas ALWAYS take the 1024px preview. There is no LOD
//     switch: zooming in does not promote a node to the original. A canvas
//     holding 60 outputs must never pull 60 originals.
//   • The consumers a user opens deliberately (lightbox, compare slider)
//     take the original via `?full=1`, because that is where pixels are
//     inspected.
//
// Fixtures use the REAL durable url shape (`/api/v1/generated-media/{id}/
// cover`) — the sibling suites' `/gm/1/cover` shorthand does not match the
// helper's pattern and would make every assertion here vacuously true.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));
vi.mock('../../../../services/resourceService', () => ({
  getResourceFileUrl: (id: string) => `https://example.test/${id}`,
}));
vi.mock('../../../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: 't' } } }),
    },
  }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputLightbox } from './OutputLightbox';
import { OutputNodeView } from './OutputNodeView';

const ITEMS = [
  { url: '/api/v1/generated-media/1/cover', name: 'first.png' },
  { url: '/api/v1/generated-media/2/cover', name: 'second.png' },
];
const SOURCES = [{ url: '/api/v1/generated-media/10/cover', name: 'input-a.png' }];

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({ canvasId: '9', kind: 'smart', loadStatus: 'ready' });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

function renderBox(overrides: Partial<Parameters<typeof OutputLightbox>[0]> = {}) {
  return render(
    <OutputLightbox
      items={ITEMS}
      index={0}
      kind="image"
      onIndexChange={vi.fn()}
      onClose={vi.fn()}
      compareSources={SOURCES}
      {...overrides}
    />,
  );
}

describe('lightbox takes the original', () => {
  it('the main image asks for full resolution', () => {
    renderBox();
    const img = screen.getByTestId('lightbox-image') as HTMLImageElement;
    expect(img.src).toMatch(/[?&]full=1$/);
    expect(img.src).toContain('/api/v1/generated-media/1/cover');
  });

  it('both sides of the compare slider ask for full resolution', () => {
    renderBox();
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    const result = screen.getByTestId('compare-result') as HTMLImageElement;
    const original = screen.getByTestId('compare-original') as HTMLImageElement;
    expect(result.src).toMatch(/[?&]full=1$/);
    expect(original.src).toMatch(/[?&]full=1$/);
    expect(original.src).toContain('/api/v1/generated-media/10/cover');
  });
});

const baseProps = {
  type: 'output',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function renderNode(data: Record<string, unknown> = {}) {
  return render(
    <ReactFlowProvider>
      <OutputNodeView
        {...baseProps}
        selected={false}
        id="out1"
        data={{
          kind: 'image',
          resource_id: null,
          preview_text: '',
          preview_url: '/api/v1/generated-media/1/cover',
          crop_region: null,
          images: [{ url: '/api/v1/generated-media/1/cover', kind: 'image' }],
          ...data,
        }}
      />
    </ReactFlowProvider> as ReactNode,
  );
}

describe('canvas nodes never take the original', () => {
  it('the node image stays on the preview tier, cache-busted', () => {
    const { container } = renderNode();
    const img = container.querySelector('img') as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.src).toContain('?v=2');
    expect(img.src).not.toContain('full=1');
  });
});
