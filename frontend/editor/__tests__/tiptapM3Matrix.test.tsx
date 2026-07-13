/**
 * M3 format matrix — the TipTap-mode mirror of `formatMatrix.test.tsx`
 * (spec D5/M3 item 1/4). Every element type × both engines, exercised
 * through a MOUNTED `TipTapSceneEditor` (not the legacy layout engines):
 * per-type `hw-*`/`as-*` classes, `data-el-type`, Asian ornament
 * presence/absence, and the copilot tick's class shape. Also covers the
 * live format-switch behaviour (`SceneBlock` keys `<TipTapSceneEditor>` on
 * `format` — see that component's module doc for why).
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';

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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock('../sceneService', () => ({
  newElementId: () => 'el_ffffffff',
  updateSceneMeta: vi.fn().mockResolvedValue({}),
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
        dispatchOps: (_ops: ElementOp[], optimistic: ScriptElement[]) => setElements(optimistic),
        resolveConflict: () => {},
        flush: async () => {},
      };
    },
  };
});

import { TipTapSceneEditor } from '../tiptap/TipTapSceneEditor';
import { SceneBlock } from '../components/SceneBlock';

const ALL_TYPES: ElementType[] = [
  'action',
  'character',
  'dialogue',
  'paren',
  'transition',
  'comment',
  'subtitle',
];

const oneOf = (type: ElementType): ScriptElement[] => [
  { id: 'el_00000001', type, text: 'Sample line', character_id: null },
];

const noopDispatch = () => {};

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('M3 format matrix — every element type × both engines (mounted TipTap)', () => {
  it.each(ALL_TYPES)('Hollywood renders hw-%s + data-el-type, no as-row wrapper', async (type) => {
    const { container } = render(
      <TipTapSceneEditor initialElements={oneOf(type)} format="hollywood" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(container.querySelector('[data-el-id="el_00000001"]')).not.toBeNull());
    const node = container.querySelector('[data-el-id="el_00000001"]') as HTMLElement;
    expect(node).toHaveClass(`hw-${type}`);
    expect(node.getAttribute('data-el-type')).toBe(type);
    expect(container.querySelector('.as-row')).toBeNull();
    // The row's own DOM (what PM tracks as the node) is the bare `.mh-el-row`.
    expect(container.querySelector('.mh-el-row')).not.toBeNull();
  });

  it.each(ALL_TYPES)('Asian renders as-%s + as-row-%s wrapper + data-el-type on both', async (type) => {
    const { container } = render(
      <TipTapSceneEditor initialElements={oneOf(type)} format="asian" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(container.querySelector('[data-el-id="el_00000001"]')).not.toBeNull());
    const node = container.querySelector('[data-el-id="el_00000001"]') as HTMLElement;
    expect(node).toHaveClass(`as-${type}`);
    expect(node.getAttribute('data-el-type')).toBe(type);

    const wrapper = container.querySelector(`.as-row.as-row-${type}`) as HTMLElement;
    expect(wrapper).not.toBeNull();
    expect(wrapper.getAttribute('data-el-type')).toBe(type);
    // The inner `.mh-el-row` (byte-identical to what Hollywood renders bare)
    // nests INSIDE the `.as-row` wrapper.
    expect(wrapper.querySelector('.mh-el-row')).not.toBeNull();
  });

  it('emits the △ action prefix mark ONLY in Asian, ONLY for action', async () => {
    const hollywood = render(
      <TipTapSceneEditor initialElements={oneOf('action')} format="hollywood" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(hollywood.container.querySelector('.mh-el-row')).not.toBeNull());
    expect(hollywood.container.querySelector('.as-prefix')).toBeNull();
    cleanup();

    const asian = render(
      <TipTapSceneEditor initialElements={oneOf('action')} format="asian" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(asian.container.querySelector('.as-row')).not.toBeNull());
    const mark = asian.container.querySelector('.as-prefix') as HTMLElement;
    expect(mark).not.toBeNull();
    expect(mark.textContent).toBe('△');
    expect(mark.getAttribute('contenteditable')).toBe('false');
    expect(mark.getAttribute('aria-hidden')).toBe('true');
  });

  it('emits the ： character suffix mark ONLY in Asian, ONLY for character', async () => {
    const asian = render(
      <TipTapSceneEditor initialElements={oneOf('character')} format="asian" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(asian.container.querySelector('.as-row')).not.toBeNull());
    const mark = asian.container.querySelector('.as-suffix') as HTMLElement;
    expect(mark).not.toBeNull();
    expect(mark.textContent).toBe('：');
    expect(mark.getAttribute('contenteditable')).toBe('false');
    cleanup();

    const hollywood = render(
      <TipTapSceneEditor initialElements={oneOf('character')} format="hollywood" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(hollywood.container.querySelector('.mh-el-row')).not.toBeNull());
    expect(hollywood.container.querySelector('.as-suffix')).toBeNull();
  });

  it.each(ALL_TYPES.filter((t) => t !== 'action' && t !== 'character'))(
    'no ornament mark for %s in Asian format',
    async (type) => {
      const { container } = render(
        <TipTapSceneEditor initialElements={oneOf(type)} format="asian" dispatchOps={noopDispatch} />,
      );
      await waitFor(() => expect(container.querySelector('.as-row')).not.toBeNull());
      expect(container.querySelector('.as-prefix')).toBeNull();
      expect(container.querySelector('.as-suffix')).toBeNull();
    },
  );

  it.each(ALL_TYPES)('tick renders `.mh-el-tick.t-%s` (span, no button) with no onTickClick', async (type) => {
    const { container } = render(
      <TipTapSceneEditor initialElements={oneOf(type)} format="hollywood" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(container.querySelector('.mh-el-tick')).not.toBeNull());
    const tick = container.querySelector('.mh-el-tick') as HTMLElement;
    expect(tick.tagName).toBe('SPAN');
    expect(tick).toHaveClass(`t-${type}`);
    expect(tick).not.toHaveClass('tick-btn');
  });

  it.each(ALL_TYPES)('tick renders `.mh-el-tick.tick-btn.t-%s` (button) with onTickClick set', async (type) => {
    const { container } = render(
      <TipTapSceneEditor
        initialElements={oneOf(type)}
        format="hollywood"
        dispatchOps={noopDispatch}
        onTickClick={() => {}}
      />,
    );
    await waitFor(() => expect(container.querySelector('.mh-el-tick')).not.toBeNull());
    const tick = container.querySelector('.mh-el-tick') as HTMLElement;
    expect(tick.tagName).toBe('BUTTON');
    expect(tick).toHaveClass('tick-btn');
    expect(tick).toHaveClass(`t-${type}`);
  });
});

describe('M3 — live format switch (SceneBlock keys TipTapSceneEditor on format)', () => {
  it('flips from hw-action to as-action (with the △ mark) on a format prop change', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    const scene: SceneDoc = {
      id: '900',
      script_id: '1',
      chapter_id: null,
      heading_int_ext: 'INT',
      location_text: 'Studio',
      time_of_day: 'NIGHT',
      content_version: 1,
      sort_order: 0,
      elements: [{ id: 'el_00000001', type: 'action', text: 'One pool of light.', character_id: null }],
    };
    const { container, rerender } = render(<SceneBlock scene={scene} index={0} format="hollywood" />);
    await waitFor(() => expect(container.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
    expect(container.querySelector('.hw-action')).not.toBeNull();
    expect(container.querySelector('.as-prefix')).toBeNull();

    rerender(<SceneBlock scene={scene} index={0} format="asian" />);
    await waitFor(() => expect(container.querySelector('.as-prefix')).not.toBeNull());
    expect(container.querySelector('.as-prefix')?.textContent).toBe('△');
    expect(container.querySelector('.hw-action')).toBeNull();
  });
});
