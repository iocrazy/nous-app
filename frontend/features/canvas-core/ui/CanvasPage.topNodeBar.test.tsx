/**
 * Who gets the top node bar (P4 final wave, item 7 / T6 concern 1).
 *
 * The bar was gated on `kind === 'smart'` while the four entity kinds seeded a
 * preset workflow and had nothing to add. P4 deleted those templates, so a
 * hand-made character / location / prop / costume board opened BLANK with no
 * visible way to add a node — the drag-create menu is still there, but a user
 * has to already know it is.
 *
 * The three kinds asserted false are asserted for three different reasons, and
 * that is why they are all here: `lite` withholds the full node set on purpose
 * (`DragCreateMenu` cuts `SMART_ITEMS` down to four), `storyboard` is a
 * different surface entirely, and a read-only session must not offer create
 * actions on ANY kind.
 */

import React from 'react';
import { render, cleanup, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SCOPE = '727145299382534100';

let params: Record<string, string | undefined> = { canvasId: 'c1', teamId: SCOPE };
vi.mock('react-router-dom', () => ({
  useParams: () => params,
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams()],
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast: vi.fn() }),
  useToast: () => ({ addToast: vi.fn() }),
}));

vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView: vi.fn() });
    }, [onInit]);
    return <div data-testid="canvas-surface" />;
  },
}));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('../realtime/useCanvasRealtime', () => ({ useCanvasRealtime: () => {} }));
vi.mock('./useCanvasShortcuts', () => ({ useCanvasShortcuts: () => {} }));

// Rendered as a MARKER, not stubbed to null: this file is about whether the
// bar is mounted at all, which a `() => null` mock cannot tell you.
vi.mock('./TopNodeBar', () => ({
  TopNodeBar: () => <div data-testid="top-node-bar" />,
}));

vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: vi.fn().mockResolvedValue({ data: [] }),
}));
vi.mock('../../../editor/sceneService', () => ({
  listScenes: vi.fn().mockResolvedValue([]),
  listShots: vi.fn().mockResolvedValue([]),
  createShot: vi.fn(),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

function seedReady(over: Record<string, unknown>) {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind: 'smart',
    projectId: 'proj-1',
    nodes: [],
    connections: [],
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    ...over,
  } as never);
}

const renderView = () => render(<CanvasView canvasId="c1" teamId={SCOPE} />);

beforeEach(() => {
  params = { canvasId: 'c1', teamId: SCOPE };
  useCanvasCoreStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('top node bar visibility by canvas kind', () => {
  it.each(['smart', 'character', 'location', 'prop', 'costume'])(
    'shows the bar on a %s board',
    (kind) => {
      seedReady({ kind });
      renderView();
      expect(screen.getByTestId('top-node-bar')).toBeInTheDocument();
    },
  );

  it('withholds it on a lite board, which offers a narrower menu on purpose', () => {
    seedReady({ kind: 'lite' });
    renderView();
    expect(screen.queryByTestId('top-node-bar')).toBeNull();
  });

  it('withholds it on a storyboard, a different surface entirely', () => {
    seedReady({ kind: 'storyboard' });
    renderView();
    expect(screen.queryByTestId('top-node-bar')).toBeNull();
  });

  it('withholds it from a read-only viewer on an entity board', () => {
    // The gate is `kind && !readOnly`; widening the kind half must not have
    // handed create actions to someone who cannot save.
    seedReady({ kind: 'character', readOnly: true });
    renderView();
    expect(screen.queryByTestId('top-node-bar')).toBeNull();
  });
});
