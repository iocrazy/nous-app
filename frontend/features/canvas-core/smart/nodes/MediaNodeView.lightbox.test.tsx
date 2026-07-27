/**
 * MediaNodeView lightbox wiring (spec §7.1): clicking a thumbnail opens
 * OutputLightbox at that item's index — image items open the image
 * lightbox, video items open the video lightbox. Harness copied from
 * MediaNodeView.test.tsx.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MediaNodeView } from './MediaNodeView';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

function seedNode(items: Array<{ url: string; kind: string; name?: string }> = []) {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [
      {
        id: 'm1',
        type: 'media',
        position: { x: 0, y: 0 },
        data: { title: 'Media', items },
      },
    ],
  } as never);
}

function nodeData(): { items?: Array<{ url: string }>; uploading?: number } {
  return (useCanvasCoreStore.getState().nodes[0] as {
    data: { items?: Array<{ url: string }>; uploading?: number };
  }).data;
}

function renderView(items: Array<{ url: string; kind: string; name?: string }> = []) {
  seedNode(items);
  const props = {
    id: 'm1',
    data: nodeData(),
    selected: false,
  } as unknown as Parameters<typeof MediaNodeView>[0];
  return render(<MediaNodeView {...props} />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('MediaNodeView lightbox wiring (spec §7.1)', () => {
  it('clicking an image thumbnail opens the lightbox showing that image', () => {
    renderView([
      { url: '/api/v1/generated-media/1/cover', kind: 'image' },
      { url: '/api/v1/generated-media/2/cover', kind: 'image' },
    ]);
    fireEvent.click(screen.getByTestId('media-node-thumb-1'));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    expect(screen.getByTestId('lightbox-counter')).toHaveTextContent('2 / 2');
  });

  it('clicking a video thumbnail opens the lightbox in video kind', () => {
    renderView([
      { url: '/api/v1/generated-media/1/cover', kind: 'image' },
      { url: '/api/v1/generated-media/9/stream', kind: 'video' },
    ]);
    fireEvent.click(screen.getByTestId('media-node-video-1'));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    expect(screen.getByTestId('lightbox-video')).toBeTruthy();
  });

  it('close callback dismisses the lightbox', () => {
    renderView([{ url: '/api/v1/generated-media/1/cover', kind: 'image' }]);
    fireEvent.click(screen.getByTestId('media-node-thumb-0'));
    expect(screen.getByTestId('output-lightbox')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByTestId('output-lightbox')).toBeNull();
  });

  it('thumbnail click target carries the nodrag class', () => {
    renderView([{ url: '/api/v1/generated-media/1/cover', kind: 'image' }]);
    const trigger = screen.getByTestId('media-node-thumb-0');
    expect(trigger.className).toContain('nodrag');
  });
});
