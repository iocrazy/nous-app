// features/canvas-core/smart/nodes/OutputNodeView.toolbar.test.tsx
// Toolbar integration (P2-3): the floating toolbar mounts on media-backed
// output nodes, pins visible while selected, and its Preview opens the
// lightbox IMMEDIATELY — bypassing the 250ms crop-disambiguation delay.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
import { OutputNodeView } from './OutputNodeView';

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({ canvasId: '9', kind: 'smart', loadStatus: 'ready' });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
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
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function renderNode(selected = true, data: Record<string, unknown> = {}) {
  return render(
    <Wrap>
      <OutputNodeView
        {...baseProps}
        selected={selected}
        id="out1"
        data={{
          kind: 'image',
          resource_id: null,
          preview_text: '',
          preview_url: null,
          crop_region: null,
          images: [{ url: '/gm/1/cover', kind: 'image' }],
          ...data,
        }}
      />
    </Wrap>,
  );
}

describe('OutputNodeView floating toolbar (P2-3)', () => {
  it('mounts the toolbar on media nodes; text nodes get none', () => {
    renderNode();
    expect(screen.getByTestId('output-node-toolbar')).toBeTruthy();
    cleanup();
    renderNode(true, { kind: 'text', images: [], preview_text: 'hi' });
    expect(screen.queryByTestId('output-node-toolbar')).toBeNull();
  });

  it('pins visible while selected (hover reveal handles the rest)', () => {
    renderNode(true);
    const bar = screen.getByTestId('output-node-toolbar');
    expect(bar.className).toContain('opacity-100');
    cleanup();
    renderNode(false);
    expect(screen.getByTestId('output-node-toolbar').className).toContain('opacity-0');
  });

  it('Preview opens the lightbox immediately — no 250ms delay', () => {
    vi.useFakeTimers();
    renderNode();
    fireEvent.click(
      screen
        .getByTestId('output-node-toolbar')
        .querySelector('button[aria-label="Preview"]')!,
    );
    // No timer advance: the lightbox must already be open.
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
  });
});


describe('IC editing keys on the toolbar (⑥)', () => {
  it('exposes Crop/Expand/Mask/Split alongside Preview/Download', async () => {
    const { OutputNodeToolbar } = await import('./OutputNodeToolbar');
    const { render: r, screen: s } = await import('@testing-library/react');
    r(
      <OutputNodeToolbar
        items={[{ url: '/api/v1/generated-media/1/cover' }]}
        onPreview={() => {}}
        onCrop={() => {}}
        onExpand={() => {}}
        onMask={() => {}}
        onSplit={() => {}}
        pinned
      />,
    );
    for (const label of ['Preview', 'Crop', 'Expand', 'Mask', 'Split', 'Download']) {
      expect(s.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });
});
