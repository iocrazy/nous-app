// features/canvas-core/smart/nodes/OutputNodeView.lightbox.test.tsx
// Lightbox wiring on the output node (P2-5 flipped gesture): DOUBLE-click an
// image opens the lightbox at that index; a single click does NOT (it falls
// through to React Flow node selection). Crop moved to a header chip.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
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

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

const IMAGE_DATA = {
  kind: 'image',
  resource_id: null,
  preview_text: '',
  preview_url: '/gm/1/cover',
  crop_region: null,
  images: [
    { url: '/gm/1/cover', kind: 'image' },
    { url: '/gm/2/cover', kind: 'image' },
  ],
};

describe('OutputNodeView lightbox wiring (P2-5)', () => {
  it('double-click on a grid image opens the EDITOR on that image (IC)', () => {
    renderOutput(IMAGE_DATA);
    fireEvent.doubleClick(screen.getAllByRole('img')[1]);
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
  });

  it('single click does NOT open the lightbox (selection falls through)', () => {
    renderOutput({ ...IMAGE_DATA, images: [{ url: '/gm/1/cover', kind: 'image' }] });
    fireEvent.click(screen.getByRole('img'));
    expect(screen.queryByTestId('output-lightbox')).toBeNull();
  });
});

describe('video output entry (G7 review #2)', () => {
  it('double-click on the video preview opens the video lightbox', () => {
    renderOutput({
      kind: 'video',
      resource_id: null,
      preview_text: '',
      preview_url: '/gm/9/stream',
      crop_region: null,
    });
    fireEvent.doubleClick(screen.getByTestId('output-video-preview'));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    expect(screen.getByTestId('lightbox-video')).toBeTruthy();
  });
});
