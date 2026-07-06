/**
 * NodesView + Scenes-slot routing tests (Phase B Task 2).
 *
 * React Flow is stubbed down to a prop-capturing shim (the repo's canvas-core
 * test pattern) so we can drive the real drag-stop / double-click callbacks the
 * view wires up and assert what they do, without needing a laid-out canvas in
 * jsdom. Covers: both node types projected, debounced coordinate persistence,
 * chapter drags NOT persisting, double-click jump-to-script, and the rail
 * Scenes slot toggling the central view (with aria-current following).
 */
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Capture the props React Flow renders with so we can invoke the callbacks the
// view registers. Returning null keeps Background/Controls (which need provider
// context) from mounting — we only care about the wired props.
let capturedProps: Record<string, unknown> = {};
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      capturedProps = props;
      return null;
    },
  };
});

const svc = vi.hoisted(() => ({
  updateSceneMeta: vi.fn(),
  listScenes: vi.fn(),
  convertToScenes: vi.fn(),
  moveScene: vi.fn(),
  createScene: vi.fn(),
  applyOps: vi.fn(),
  newElementId: () => 'el_test0001',
}));
vi.mock('../sceneService', () => svc);

vi.mock('../../services/scriptService', () => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

import { NodesView } from '../nodes/NodesView';
import { EditorShell } from '../components/EditorShell';

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '200',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  position_x: null,
  position_y: null,
  elements: [{ id: 'el_00000001', type: 'action', text: 'One pool of light.' }],
  ...over,
});

const chapter = (over: Partial<ScriptChapter> & { id: string }): ScriptChapter => ({
  script_id: '1',
  chapter_number: 1,
  title: 'Act One',
  position_x: 0,
  position_y: 0,
  data_json: {},
  sort_order: 0,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
  ...over,
});

function findNode(type: string): Record<string, unknown> {
  const nodes = capturedProps.nodes as Array<Record<string, unknown>>;
  const node = nodes.find((n) => n.type === type);
  if (!node) throw new Error(`no ${type} in captured nodes`);
  return node;
}

afterEach(() => {
  cleanup();
  capturedProps = {};
  vi.clearAllMocks();
});

describe('NodesView', () => {
  it('projects both chapter and scene nodes into the flow', () => {
    render(
      <NodesView
        scenes={[scene({ id: '200', chapter_id: '100' }), scene({ id: '201', chapter_id: null })]}
        chapters={[chapter({ id: '100' })]}
        onOpenScene={vi.fn()}
        scriptId="1"
        onReload={vi.fn()}
      />,
    );
    const nodes = capturedProps.nodes as Array<Record<string, unknown>>;
    expect(nodes).toHaveLength(3);
    expect(nodes.filter((n) => n.type === 'sceneNode')).toHaveLength(2);
    expect(nodes.filter((n) => n.type === 'chapterNode')).toHaveLength(1);
    // Both custom node types are registered with React Flow.
    expect(Object.keys(capturedProps.nodeTypes as object)).toEqual(
      expect.arrayContaining(['sceneNode', 'chapterNode']),
    );
  });

  describe('coordinate persistence', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('persists a dragged scene position after the debounce window', () => {
      svc.updateSceneMeta.mockResolvedValue(scene({}));
      render(
        <NodesView
          scenes={[scene({ id: '200' })]}
          chapters={[]}
          onOpenScene={vi.fn()}
          scriptId="1"
          onReload={vi.fn()}
        />,
      );
      const sceneNode = { ...findNode('sceneNode'), position: { x: 250.4, y: 360.7 } };

      (capturedProps.onNodeDragStop as (e: unknown, n: unknown) => void)({}, sceneNode);
      // Nothing written before the debounce elapses.
      expect(svc.updateSceneMeta).not.toHaveBeenCalled();

      vi.advanceTimersByTime(500);
      expect(svc.updateSceneMeta).toHaveBeenCalledWith('200', {
        position_x: 250,
        position_y: 361,
      });
    });

    it('does not persist when a read-only chapter node is dragged', () => {
      render(
        <NodesView
          scenes={[]}
          chapters={[chapter({ id: '100' })]}
          onOpenScene={vi.fn()}
          scriptId="1"
          onReload={vi.fn()}
        />,
      );
      const chapterNode = { ...findNode('chapterNode'), position: { x: 10, y: 20 } };

      (capturedProps.onNodeDragStop as (e: unknown, n: unknown) => void)({}, chapterNode);
      vi.advanceTimersByTime(500);
      expect(svc.updateSceneMeta).not.toHaveBeenCalled();
    });
  });

  it('jumps to the scene on double-click via onOpenScene', () => {
    const onOpenScene = vi.fn();
    render(
      <NodesView
        scenes={[scene({ id: '200' })]}
        chapters={[]}
        onOpenScene={onOpenScene}
        scriptId="1"
        onReload={vi.fn()}
      />,
    );

    (capturedProps.onNodeDoubleClick as (e: unknown, n: unknown) => void)({}, findNode('sceneNode'));
    expect(onOpenScene).toHaveBeenCalledWith('200');
  });
});

describe('EditorShell — Scenes slot routing', () => {
  beforeEach(() => {
    // The jump-back effect calls scrollIntoView, which jsdom does not implement.
    HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  // Renders the full editor shell — heavier than the unit cases, so it gets a
  // longer ceiling to stay green under the suite's parallel load.
  it('switches the central view to the node canvas and back on scene open', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument(), { timeout: 15000 });

    // Default view is the script sheet — no node canvas yet.
    expect(screen.queryByTestId('nodes-view')).toBeNull();

    const scenesSlot = screen.getByRole('button', { name: /moduleScenes/ });
    fireEvent.click(scenesSlot);

    // Node canvas mounts and the slot reflects the active view.
    expect(screen.getByTestId('nodes-view')).toBeInTheDocument();
    expect(scenesSlot).toHaveAttribute('aria-current', 'page');

    // Double-clicking a scene node routes back to the script sheet.
    act(() => {
      (capturedProps.onNodeDoubleClick as (e: unknown, n: unknown) => void)(
        {},
        findNode('sceneNode'),
      );
    });
    expect(screen.queryByTestId('nodes-view')).toBeNull();
    expect(screen.getByRole('button', { name: /moduleScript/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
  }, 20000);
});
