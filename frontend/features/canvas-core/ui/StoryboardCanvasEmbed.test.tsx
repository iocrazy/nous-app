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

  // Task 7 (shot-nodes-on-canvas epic — keep-alive 显隐切换): the host page
  // (EpisodeStoryboardPage) is now kept mounted-but-hidden across module
  // switches, so this embed must stop resolving/mounting `CanvasView` while
  // `active` is false — CanvasView owns a SINGLETON store, and letting it run
  // in the background indefinitely (autosave, realtime, reconcile) for a
  // canvas nobody is looking at is exactly what the `active` prop exists to
  // prevent. See that prop's doc comment for the full rationale.
  describe('active prop (Task 7 keep-alive)', () => {
    it('does not resolve or mount CanvasView while inactive', async () => {
      render(<StoryboardCanvasEmbed episodeId="ep-1" active={false} />);

      // Give any stray microtask a chance to run, then assert nothing fired.
      await Promise.resolve();
      expect(getOrCreateStoryboardCanvas).not.toHaveBeenCalled();
      expect(screen.queryByTestId('storyboard-canvas-embed-loading')).toBeNull();
      expect(screen.queryByTestId('canvas-view-mock')).toBeNull();
    });

    it('unmounts CanvasView (releasing the singleton store) when active flips from true to false', async () => {
      getOrCreateStoryboardCanvas.mockResolvedValue({ id: 'cv-1', kind: 'storyboard' });
      const { rerender } = render(<StoryboardCanvasEmbed episodeId="ep-1" active />);
      await waitFor(() => expect(screen.getByTestId('canvas-view-mock')).toBeInTheDocument());

      rerender(<StoryboardCanvasEmbed episodeId="ep-1" active={false} />);

      expect(screen.queryByTestId('canvas-view-mock')).toBeNull();
    });

    it('re-resolves and re-mounts CanvasView when active flips back to true', async () => {
      getOrCreateStoryboardCanvas.mockResolvedValueOnce({ id: 'cv-1', kind: 'storyboard' });
      const { rerender } = render(<StoryboardCanvasEmbed episodeId="ep-1" active />);
      await waitFor(() => expect(screen.getByTestId('canvas-view-mock')).toBeInTheDocument());

      rerender(<StoryboardCanvasEmbed episodeId="ep-1" active={false} />);
      expect(screen.queryByTestId('canvas-view-mock')).toBeNull();
      getOrCreateStoryboardCanvas.mockClear();

      getOrCreateStoryboardCanvas.mockResolvedValueOnce({ id: 'cv-1', kind: 'storyboard' });
      rerender(<StoryboardCanvasEmbed episodeId="ep-1" active />);

      await waitFor(() => expect(getOrCreateStoryboardCanvas).toHaveBeenCalledWith('ep-1'));
      await waitFor(() => expect(screen.getByTestId('canvas-view-mock')).toBeInTheDocument());
    });
  });
});
