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

import { MiniMap } from '@xyflow/react';
import { NodesView } from '../nodes/NodesView';
import { EditorShell } from '../components/EditorShell';

/** Depth-first search for a rendered child element whose component type matches. */
function hasChildOfType(children: unknown, type: unknown): boolean {
  const stack = Array.isArray(children) ? [...children] : [children];
  while (stack.length) {
    const node = stack.pop() as { type?: unknown; props?: { children?: unknown } } | null;
    if (!node || typeof node !== 'object') continue;
    if (node.type === type) return true;
    const kids = node.props?.children;
    if (kids != null) stack.push(...(Array.isArray(kids) ? kids : [kids]));
  }
  return false;
}

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

  describe('selection system', () => {
    it('wires rubber-band + multi-select props into React Flow', () => {
      render(
        <NodesView
          scenes={[scene({ id: '200' })]}
          chapters={[]}
          onOpenScene={vi.fn()}
          scriptId="1"
          onReload={vi.fn()}
        />,
      );
      // Shift+drag on blank = rubber band; partial = intersect-to-select.
      expect(capturedProps.selectionMode).toBe('partial');
      expect(capturedProps.selectionKeyCode).toBe('Shift');
      // Ctrl/Cmd toggles add-to-selection — platform-detected modifier.
      expect(['Meta', 'Control']).toContain(capturedProps.multiSelectionKeyCode);
      // Group drag settles through its own callback.
      expect(typeof capturedProps.onSelectionDragStop).toBe('function');
    });

    describe('group-drag persistence', () => {
      beforeEach(() => vi.useFakeTimers());
      afterEach(() => vi.useRealTimers());

      it('persists every selected scene once after a group drag', () => {
        svc.updateSceneMeta.mockResolvedValue(scene({}));
        render(
          <NodesView
            scenes={[scene({ id: '200' }), scene({ id: '201' })]}
            chapters={[]}
            onOpenScene={vi.fn()}
            scriptId="1"
            onReload={vi.fn()}
          />,
        );
        const nodes = capturedProps.nodes as Array<Record<string, unknown>>;
        const dragged = nodes
          .filter((n) => n.type === 'sceneNode')
          .map((n, i) => ({ ...n, selected: true, position: { x: 10 + i, y: 20 + i } }));

        (capturedProps.onSelectionDragStop as (e: unknown, n: unknown) => void)({}, dragged);
        expect(svc.updateSceneMeta).not.toHaveBeenCalled();

        vi.advanceTimersByTime(500);
        expect(svc.updateSceneMeta).toHaveBeenCalledTimes(2);
        expect(svc.updateSceneMeta).toHaveBeenCalledWith('200', { position_x: 10, position_y: 20 });
        expect(svc.updateSceneMeta).toHaveBeenCalledWith('201', { position_x: 11, position_y: 21 });
      });

      it('skips a chapter node caught in a mixed group selection', () => {
        svc.updateSceneMeta.mockResolvedValue(scene({}));
        render(
          <NodesView
            scenes={[scene({ id: '200' })]}
            chapters={[chapter({ id: '100' })]}
            onOpenScene={vi.fn()}
            scriptId="1"
            onReload={vi.fn()}
          />,
        );
        const nodes = capturedProps.nodes as Array<Record<string, unknown>>;
        const dragged = nodes
          .filter((n) => n.type === 'sceneNode' || n.type === 'chapterNode')
          .map((n) => ({ ...n, selected: true, position: { x: 5, y: 6 } }));

        (capturedProps.onSelectionDragStop as (e: unknown, n: unknown) => void)({}, dragged);
        vi.advanceTimersByTime(500);
        // Only the scene node persists; the chapter is projection-only in Task 1.
        expect(svc.updateSceneMeta).toHaveBeenCalledTimes(1);
        expect(svc.updateSceneMeta).toHaveBeenCalledWith('200', { position_x: 5, position_y: 6 });
      });

      it('persists the whole selection when one selected node is dragged solo', () => {
        // React Flow routes a solo drag of a node that sits inside an active
        // multi-selection through onNodeDragStop (not onSelectionDragStop); the
        // view must still flush every selected scene, not just the grabbed one.
        svc.updateSceneMeta.mockResolvedValue(scene({}));
        render(
          <NodesView
            scenes={[scene({ id: '200' }), scene({ id: '201' })]}
            chapters={[]}
            onOpenScene={vi.fn()}
            scriptId="1"
            onReload={vi.fn()}
          />,
        );
        const sceneIds = (capturedProps.nodes as Array<Record<string, unknown>>)
          .filter((n) => n.type === 'sceneNode')
          .map((n) => n.id as string);
        // Select both scene nodes through the real change pipeline.
        act(() => {
          (capturedProps.onNodesChange as (c: unknown[]) => void)(
            sceneIds.map((id) => ({ id, type: 'select', selected: true })),
          );
        });

        // Grab one selected node and drop it individually.
        (capturedProps.onNodeDragStop as (e: unknown, n: unknown) => void)({}, findNode('sceneNode'));
        vi.advanceTimersByTime(500);

        expect(svc.updateSceneMeta).toHaveBeenCalledTimes(2);
        expect(svc.updateSceneMeta.mock.calls.map((c) => c[0]).sort()).toEqual(['200', '201']);
      });
    });
  });

  describe('navigation & feel', () => {
    it('enables snap grid, snap-to-grid, and visible-only rendering', () => {
      render(
        <NodesView
          scenes={[scene({ id: '200' })]}
          chapters={[]}
          onOpenScene={vi.fn()}
          scriptId="1"
          onReload={vi.fn()}
        />,
      );
      expect(capturedProps.snapGrid).toEqual([8, 8]);
      expect(capturedProps.snapToGrid).toBe(true);
      expect(capturedProps.onlyRenderVisibleElements).toBe(true);
      // Alignment guides + nudge need the live instance and per-drag hook.
      expect(typeof capturedProps.onInit).toBe('function');
      expect(typeof capturedProps.onNodeDrag).toBe('function');
    });

    it('mounts a MiniMap inside the canvas', () => {
      render(
        <NodesView
          scenes={[scene({ id: '200' })]}
          chapters={[]}
          onOpenScene={vi.fn()}
          scriptId="1"
          onReload={vi.fn()}
        />,
      );
      expect(hasChildOfType(capturedProps.children, MiniMap)).toBe(true);
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
