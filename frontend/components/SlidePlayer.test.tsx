import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
