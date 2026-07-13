import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  updateSceneMeta: vi.fn().mockResolvedValue({}),
  newElementId: () => 'el_new',
}));
vi.mock('../sceneService', () => ({
  newElementId: svc.newElementId,
  updateSceneMeta: svc.updateSceneMeta,
}));

const sync = vi.hoisted(() => ({ dispatch: vi.fn() }));
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
        applyRemoteOps: vi.fn(),
        reconcile: vi.fn(),
        flush: async () => {},
      };
    },
  };
});

import { SceneBlock } from '../components/SceneBlock';

const scene = (): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_a', type: 'action', text: '' }],
});

const line = (): HTMLElement => document.querySelector('[data-el-id="el_a"]') as HTMLElement;

/** Simulate typing into the uncontrolled contenteditable. */
const typeText = (node: HTMLElement, text: string) => {
  node.textContent = text;
  fireEvent.input(node);
};

afterEach(() => {
  cleanup();
  sync.dispatch.mockClear();
});

describe('slash menu', () => {
  it('typing "/" at block start opens the type picker; Enter applies the active type', () => {
    render(<SceneBlock scene={scene()} index={0} />);
    const node = line();

    typeText(node, '/');
    expect(screen.getByTestId('slash-menu')).toBeInTheDocument();
    // Full vocabulary (7 element types), action first.
    expect(screen.getAllByRole('option')).toHaveLength(7);

    // ArrowDown → 2nd item (character); Enter applies it.
    fireEvent.keyDown(node, { key: 'ArrowDown' });
    fireEvent.keyDown(node, { key: 'Enter' });

    const [ops] = sync.dispatch.mock.calls.at(-1)!;
    expect(ops).toEqual([
      { op: 'update', element_id: 'el_a', payload: { type: 'character', text: '' } },
    ]);
    // The `/` query text is cleared from the DOM node and the menu closes.
    expect(node.textContent).toBe('');
    expect(screen.queryByTestId('slash-menu')).toBeNull();
  });

  it('the text after "/" filters the list; click applies', () => {
    render(<SceneBlock scene={scene()} index={0} />);
    const node = line();

    typeText(node, '/tra');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1); // transition
    fireEvent.click(options[0]);

    const [ops] = sync.dispatch.mock.calls.at(-1)!;
    expect(ops).toEqual([
      { op: 'update', element_id: 'el_a', payload: { type: 'transition', text: '' } },
    ]);
  });

  it('Escape closes without dispatching; deleting the "/" closes too', () => {
    render(<SceneBlock scene={scene()} index={0} />);
    const node = line();

    typeText(node, '/');
    fireEvent.keyDown(node, { key: 'Escape' });
    expect(screen.queryByTestId('slash-menu')).toBeNull();
    expect(sync.dispatch).not.toHaveBeenCalled();

    typeText(node, '/');
    expect(screen.getByTestId('slash-menu')).toBeInTheDocument();
    typeText(node, ''); // writer deleted the slash
    expect(screen.queryByTestId('slash-menu')).toBeNull();
  });
});
