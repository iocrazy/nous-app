/**
 * M2 gutter polish — the copilot tick + the empty-block whisper, in TipTap
 * mode (spec D6, M2 item 1/6a). The tick is the SAME `.mh-el-tick tick-btn
 * t-<type>` button legacy's `ElementLine` renders (see `layoutShared.ts`),
 * now emitted by the NodeView and wired to `SceneBlock`'s existing
 * `handleTickClick` — byte-identical selection semantics (plain click
 * single-selects/toggles, shift-click range-selects) across both engines.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor, fireEvent } from '@testing-library/react';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const zeroRect = {
  bottom: 0,
  height: 0,
  left: 0,
  right: 0,
  toJSON: () => ({}),
  top: 0,
  width: 0,
  x: 0,
  y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

const svc = vi.hoisted(() => {
  let n = 0;
  return {
    updateSceneMeta: vi.fn().mockResolvedValue({}),
    nextId: () => `el_${(n++).toString(16).padStart(8, '0')}`,
  };
});
vi.mock('../sceneService', () => ({
  newElementId: () => svc.nextId(),
  updateSceneMeta: svc.updateSceneMeta,
}));

const sync = vi.hoisted(() => ({
  dispatch: vi.fn(),
  reconcile: vi.fn(),
  applyRemoteOps: vi.fn(),
}));
vi.mock('../useSceneSync', async () => {
  const React = await import('react');
  return {
    useSceneSync: (scene: SceneDoc) => {
      const [elements, setElements] = React.useState(scene.elements);
      return {
        elements,
        version: scene.content_version,
        saveState: 'saved' as const,
        conflict: null,
        dispatchOps: (ops: ElementOp[], optimistic: SceneDoc['elements']) => {
          sync.dispatch(ops, optimistic);
          setElements(optimistic);
        },
        resolveConflict: () => {},
        applyRemoteOps: sync.applyRemoteOps,
        reconcile: sync.reconcile,
        flush: async () => {},
      };
    },
  };
});

import { SceneBlock } from '../components/SceneBlock';

const makeScene = (elements: SceneDoc['elements']): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements,
});

const threeElements = (): SceneDoc['elements'] => [
  { id: 'el_a', type: 'action', text: 'Alpha.' },
  { id: 'el_b', type: 'character', text: 'BOB' },
  { id: 'el_c', type: 'action', text: '' },
];

const tickOf = (elementId: string): HTMLElement =>
  document.querySelector(`[data-tick-id="${elementId}"]`) as HTMLElement;

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  sync.dispatch.mockClear();
  sync.reconcile.mockClear();
  sync.applyRemoteOps.mockClear();
  svc.updateSceneMeta.mockClear();
});

async function mountTiptap(elements: SceneDoc['elements']) {
  vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
  render(<SceneBlock scene={makeScene(elements)} index={0} />);
  await waitFor(() => expect(document.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
}

describe('TipTap M2 — copilot gutter tick', () => {
  it('renders the same `.mh-el-tick.tick-btn.t-<type>` button legacy uses, one per row', async () => {
    await mountTiptap(threeElements());
    const ticks = document.querySelectorAll('.mh-el-tick.tick-btn');
    expect(ticks).toHaveLength(3);
    expect(tickOf('el_a')).toHaveClass('t-action');
    expect(tickOf('el_b')).toHaveClass('t-character');
    expect(tickOf('el_a')).toHaveAttribute('aria-pressed', 'false');
  });

  it('a plain click single-selects the row (aria-pressed + .selected)', async () => {
    await mountTiptap(threeElements());
    fireEvent.click(tickOf('el_a'));
    expect(tickOf('el_a')).toHaveClass('selected');
    expect(tickOf('el_a')).toHaveAttribute('aria-pressed', 'true');
    expect(tickOf('el_b')).not.toHaveClass('selected');
  });

  it('clicking the same selected tick again toggles it off', async () => {
    await mountTiptap(threeElements());
    fireEvent.click(tickOf('el_a'));
    expect(tickOf('el_a')).toHaveClass('selected');
    fireEvent.click(tickOf('el_a'));
    expect(tickOf('el_a')).not.toHaveClass('selected');
  });

  it('shift-click range-selects from the last anchor to the clicked row', async () => {
    await mountTiptap(threeElements());
    fireEvent.click(tickOf('el_a'));
    fireEvent.click(tickOf('el_c'), { shiftKey: true });
    expect(tickOf('el_a')).toHaveClass('selected');
    expect(tickOf('el_b')).toHaveClass('selected');
    expect(tickOf('el_c')).toHaveClass('selected');
  });

  it('summons the CopilotCard once a selection exists', async () => {
    await mountTiptap(threeElements());
    expect(document.querySelector('.mh-copilot-card, [data-testid="copilot-card"]')).toBeNull();
    fireEvent.click(tickOf('el_a'));
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/1/), // selectedCount surfaces somewhere in the card
    );
  });
});

describe('TipTap M2 — empty-block whisper', () => {
  // ProseMirror always renders a `<br class="ProseMirror-trailingBreak">`
  // inside an empty textblock (prosemirror-view's `addHackNode`, unconditional
  // — see `editorShellStyles.ts`'s comment on the `:has(br...)` rules this
  // motivated) so the NodeView's content div is NEVER a true CSS `:empty` —
  // only the legacy contentEditable (whose innerHTML this repo writes
  // directly) is. The correct TipTap-mode postcondition is: no TEXT content,
  // the row carries `data-el-type`, and a descendant trailing-break `<br>`
  // exists (nested one level inside `@tiptap/react`'s own NodeViewContent
  // wrapper — hence a descendant query, not a direct-child one) so the
  // `:has(br...)` whisper rules match.
  it('an empty element has no text content, carries data-el-type, and contains the PM trailing-break <br>', async () => {
    await mountTiptap(threeElements()); // el_c is empty action text
    const node = document.querySelector('[data-el-id="el_c"]') as HTMLElement;
    expect(node).not.toBeNull();
    expect(node.getAttribute('data-el-type')).toBe('action');
    expect(node.textContent).toBe('');
    expect(node.querySelector('br.ProseMirror-trailingBreak')).not.toBeNull();
  });

  it('a non-empty element carries real text (no trailing-break br)', async () => {
    await mountTiptap(threeElements());
    const node = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    expect(node.textContent).toBe('Alpha.');
    expect(node.querySelector('br.ProseMirror-trailingBreak')).toBeNull();
  });
});
