import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SlidePlayer } from './SlidePlayer';

vi.mock('../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));

// SlidePromptStrip has its own test coverage — stub it here so this file
// only asserts SlidePlayer's mount gating (resourceId present/absent).
vi.mock('./SlidePromptStrip', () => ({
  SlidePromptStrip: ({ resourceId, slideName }: { resourceId: string; slideName: string }) => (
    <div data-testid="slide-prompt-strip">{`${resourceId}:${slideName}`}</div>
  ),
}));

const slidesResponse = {
  slides: [
    { name: 'a.jpg', type: 'image', media_type: 'image/jpeg', url: '' },
    { name: 'b.jpg', type: 'image', media_type: 'image/jpeg', url: '' },
  ],
};

describe('SlidePlayer — SlidePromptStrip mount gating', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => slidesResponse,
      }),
    );
  });

  it('does not mount SlidePromptStrip when resourceId is absent (e.g. SharePage)', async () => {
    render(<SlidePlayer mediaId="m1" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByAltText('Slide 1')).toBeInTheDocument());
    expect(screen.queryByTestId('slide-prompt-strip')).toBeNull();
  });

  it('mounts SlidePromptStrip with resourceId + the current slide name when resourceId is present', async () => {
    render(<SlidePlayer mediaId="m1" resourceId="r1" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByAltText('Slide 1')).toBeInTheDocument());
    expect(screen.getByTestId('slide-prompt-strip')).toHaveTextContent('r1:a.jpg');
  });
});

/**
 * The reported "gallery has no per-slide prompt" came with "and the stage was
 * blank". Those are one fact, not two: the strip is a child of the rendered
 * carousel, so every state that short-circuits before the carousel (loading /
 * list error / empty folder) also takes the strip with it. A per-slide prompt
 * has nowhere to attach anyway — the entry is keyed by slide filename, which
 * only the slide listing provides. These cases pin that behaviour so a future
 * change doesn't render an orphan strip against a slide that isn't there.
 */
describe('SlidePlayer — strip is gated on having a slide to attach to', () => {
  it('renders no strip when the slide listing fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }));
    render(<SlidePlayer mediaId="m1" resourceId="r1" downloadStatus="failed" />);
    await waitFor(() => expect(screen.getByText(/Download Failed/i)).toBeInTheDocument());
    expect(screen.queryByTestId('slide-prompt-strip')).toBeNull();
  });

  it('renders no strip when the album has no slides', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ slides: [] }) }));
    render(<SlidePlayer mediaId="m1" resourceId="r1" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByText(/No slides available/i)).toBeInTheDocument());
    expect(screen.queryByTestId('slide-prompt-strip')).toBeNull();
  });

  it('re-targets the strip at the newly shown slide when the user advances', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => slidesResponse }));
    render(<SlidePlayer mediaId="m1" resourceId="r1" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByTestId('slide-prompt-strip')).toHaveTextContent('r1:a.jpg'));
    fireEvent.click(screen.getByLabelText('Next slide'));
    expect(screen.getByTestId('slide-prompt-strip')).toHaveTextContent('r1:b.jpg');
  });
});
