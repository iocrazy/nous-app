// features/canvas-core/smart/nodes/OutputNodeView.lightbox.test.tsx
// Lightbox wiring on the output node (G7): a single click on an image opens
// the lightbox (deferred so it never races the double-click crop editor);
// grid images open at the clicked index; a double-click opens crop only.

import { ReactFlowProvider } from '@xyflow/react';
import { act, fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
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

describe('OutputNodeView lightbox wiring', () => {
  it('single click on a grid image opens the lightbox at that index', () => {
    renderOutput(IMAGE_DATA);
    fireEvent.click(screen.getAllByRole('img')[1]);
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    expect(screen.getByText('2 / 2')).toBeTruthy();
  });

  it('double click cancels the pending lightbox (crop keeps its gesture)', () => {
    renderOutput({ ...IMAGE_DATA, images: [{ url: '/gm/1/cover', kind: 'image' }] });
    const img = screen.getByRole('img');
    fireEvent.click(img);
    fireEvent.click(img);
    fireEvent.doubleClick(img);
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByTestId('output-lightbox')).toBeNull();
  });
});

describe('video output entry (G7 review #2)', () => {
  it('renders a clickable video preview that opens the video lightbox', () => {
    renderOutput({
      kind: 'video',
      resource_id: null,
      preview_text: '',
      preview_url: '/gm/9/stream',
      crop_region: null,
    });
    const preview = screen.getByTestId('output-video-preview');
    fireEvent.click(preview);
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    expect(screen.getByTestId('lightbox-video')).toBeTruthy();
  });
});
