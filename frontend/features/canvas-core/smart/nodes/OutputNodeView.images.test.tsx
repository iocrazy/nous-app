// features/canvas-core/smart/nodes/OutputNodeView.images.test.tsx
// images[] grid rendering (G4-F2): multi-result outputs render every image;
// a single image keeps the legacy single-preview look; history-archive
// nodes are visually labelled.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

function renderOutput(data: Record<string, unknown>) {
  return render(
    <ReactFlowProvider>
      <OutputNodeView {...baseProps} id="out1" type="output" data={data} />
    </ReactFlowProvider>,
  );
}

afterEach(() => useCanvasCoreStore.getState().reset());

describe('OutputNodeView images grid', () => {
  it('renders every image of a multi-result output', () => {
    renderOutput({
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: '/gm/1/cover',
      crop_region: null,
      images: [
        { url: '/gm/1/cover', kind: 'image' },
        { url: '/gm/2/cover', kind: 'image' },
        { url: '/gm/3/cover', kind: 'image' },
      ],
    });
    expect(screen.getAllByRole('img')).toHaveLength(3);
  });

  it('single image keeps the legacy single-preview layout', () => {
    renderOutput({
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: '/gm/1/cover',
      crop_region: null,
      images: [{ url: '/gm/1/cover', kind: 'image' }],
    });
    expect(screen.getAllByRole('img')).toHaveLength(1);
  });

  it('legacy nodes without images[] still render preview_url', () => {
    renderOutput({
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: '/legacy.png',
      crop_region: null,
    });
    expect(screen.getAllByRole('img')).toHaveLength(1);
  });

  it('history-archive nodes are labelled', () => {
    renderOutput({
      kind: 'image',
      resource_id: null,
      preview_text: 'History',
      preview_url: null,
      crop_region: null,
      history_for: 'slot-1',
      images: [{ url: '/gm/old/cover', kind: 'image' }],
    });
    expect(screen.getByText(/history/i)).toBeTruthy();
  });
});
