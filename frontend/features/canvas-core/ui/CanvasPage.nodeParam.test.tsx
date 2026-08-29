/**
 * `CanvasView`'s `?node=<id>` reader — the landing half of the Generated
 * inbox's deep link (`/team/{scope}/canvas/{id}?node=n9`). A card's source
 * line is only worth clicking if it puts you in front of the node that made
 * the image, so this selects it and centres the viewport on it.
 *
 * Mocks copied from `CanvasPage.focus.test.tsx` — same harness, one extra
 * knob: `useSearchParams` is driven per test.
 */

import React from 'react';
import { act, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let searchParams = new URLSearchParams();
vi.mock('react-router-dom', () => ({
  useParams: () => ({ canvasId: 'c1' }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [searchParams],
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const fitView = vi.fn();
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView });
    }, [onInit]);
    return <div data-testid="canvas-surface" />;
  },
}));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('../realtime/useCanvasRealtime', () => ({ useCanvasRealtime: () => {} }));
vi.mock('./useCanvasShortcuts', () => ({ useCanvasShortcuts: () => {} }));

const fetchScriptProjects = vi.fn();
vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: (...args: unknown[]) => fetchScriptProjects(...args),
}));

const listScenes = vi.fn();
const listShots = vi.fn();
const createShot = vi.fn();
vi.mock('../../../editor/sceneService', () => ({
  listScenes: (...args: unknown[]) => listScenes(...args),
  listShots: (...args: unknown[]) => listShots(...args),
  createShot: (...args: unknown[]) => createShot(...args),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const seedReady = (nodes: unknown[]) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind: 'smart',
    projectId: 'proj-1',
    episodeId: 'ep-1',
    nodes: nodes as never,
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
  } as never);
};

beforeEach(() => {
  searchParams = new URLSearchParams();
  useCanvasCoreStore.getState().reset();
  fetchScriptProjects.mockReset().mockResolvedValue({ data: [] });
  listScenes.mockReset().mockResolvedValue([]);
  listShots.mockReset().mockResolvedValue([]);
  createShot.mockReset();
  fitView.mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CanvasView — ?node= deep-link reader', () => {
  it('selects the named node and centres the viewport on it', async () => {
    searchParams = new URLSearchParams('node=n9');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({ nodes: [{ id: 'n9' }] });
    expect(useCanvasCoreStore.getState().selection).toEqual(['n9']);
  });

  it('runs once per param value, not once per render', async () => {
    searchParams = new URLSearchParams('node=n9');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    const { rerender } = render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    rerender(<CanvasView canvasId="c1" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(fitView).toHaveBeenCalledTimes(1);
  });

  it('warns and does nothing for an id the canvas does not have', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    searchParams = new URLSearchParams('node=nope');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
    await waitFor(() => expect(warn).toHaveBeenCalled());
  });

  it('does nothing at all without the param', async () => {
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
  });
});
