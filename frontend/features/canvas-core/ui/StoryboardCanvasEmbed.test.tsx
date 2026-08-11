/**
 * StoryboardCanvasEmbed (Task 5) — resolves the episode's storyboard canvas
 * via get-or-create and mounts `CanvasView` on the result. `CanvasView`
 * itself is heavy (React Flow + reconcile fan-out) and already exhaustively
 * covered by `CanvasPage.shotSync.test.tsx` / `CanvasPage.focus.test.tsx` —
 * this file mocks it out to isolate the resolve/loading/error states this
 * component adds.
 */
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, dflt?: string) => dflt ?? k }),
}));

const getOrCreateStoryboardCanvas = vi.fn();
vi.mock('../services/canvasService', () => ({
  getOrCreateStoryboardCanvas: (...args: unknown[]) => getOrCreateStoryboardCanvas(...args),
}));

vi.mock('./CanvasPage', () => ({
  CanvasView: (props: Record<string, unknown>) => (
    <div data-testid="canvas-view-mock" data-props={JSON.stringify(props)} />
  ),
}));

import { StoryboardCanvasEmbed } from './StoryboardCanvasEmbed';

beforeEach(() => {
  getOrCreateStoryboardCanvas.mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('StoryboardCanvasEmbed', () => {
  it('resolves the canvas by episode id and mounts CanvasView with it', async () => {
    getOrCreateStoryboardCanvas.mockResolvedValue({ id: 'cv-1', kind: 'storyboard' });

    render(<StoryboardCanvasEmbed episodeId="ep-1" teamId="t1" />);

    expect(screen.getByTestId('storyboard-canvas-embed-loading')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('canvas-view-mock')).toBeInTheDocument());
    expect(getOrCreateStoryboardCanvas).toHaveBeenCalledWith('ep-1');
    const props = JSON.parse(screen.getByTestId('canvas-view-mock').getAttribute('data-props')!);
    expect(props.canvasId).toBe('cv-1');
    expect(props.teamId).toBe('t1');
  });

  it('threads focusShotId/onFocusHandled through to CanvasView unchanged', async () => {
    getOrCreateStoryboardCanvas.mockResolvedValue({ id: 'cv-1', kind: 'storyboard' });
    render(<StoryboardCanvasEmbed episodeId="ep-1" focusShotId="shot-7" />);
    await waitFor(() => expect(screen.getByTestId('canvas-view-mock')).toBeInTheDocument());
    const props = JSON.parse(screen.getByTestId('canvas-view-mock').getAttribute('data-props')!);
    expect(props.focusShotId).toBe('shot-7');
  });

  it('shows an error state (not stuck loading) when the resolve request fails', async () => {
    getOrCreateStoryboardCanvas.mockRejectedValue(new Error('boom'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<StoryboardCanvasEmbed episodeId="ep-1" />);

    await waitFor(() =>
      expect(screen.getByTestId('storyboard-canvas-embed-error')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('canvas-view-mock')).toBeNull();
  });

  it('re-resolves when episodeId changes (switching episodes with the tab open)', async () => {
    getOrCreateStoryboardCanvas.mockResolvedValueOnce({ id: 'cv-1', kind: 'storyboard' });
    const { rerender } = render(<StoryboardCanvasEmbed episodeId="ep-1" />);
    await waitFor(() => expect(getOrCreateStoryboardCanvas).toHaveBeenCalledWith('ep-1'));

    getOrCreateStoryboardCanvas.mockResolvedValueOnce({ id: 'cv-2', kind: 'storyboard' });
    rerender(<StoryboardCanvasEmbed episodeId="ep-2" />);

    await waitFor(() => expect(getOrCreateStoryboardCanvas).toHaveBeenCalledWith('ep-2'));
    await waitFor(() => {
      const props = JSON.parse(screen.getByTestId('canvas-view-mock').getAttribute('data-props')!);
      expect(props.canvasId).toBe('cv-2');
    });
  });
});
