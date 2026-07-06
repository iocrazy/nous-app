import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Deterministic, unique element ids so anchored inserts are assertable. Also
// stubs updateSceneMeta so the head-row debounce test can observe the call.
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

// Stateful useSceneSync mock: dispatchOps records the ops AND applies the
// optimistic list so newly-inserted rows actually render (needed for focus).
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

afterEach(() => {
  cleanup();
  sync.dispatch.mockClear();
  svc.updateSceneMeta.mockClear();
  vi.useRealTimers();
});

describe('SceneBlock element editing', () => {
  it('Tab on an action line cycles its type to character (update op)', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    const row = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    fireEvent.keyDown(row, { key: 'Tab' });

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_a', payload: { type: 'character' } });
  });

  it('Enter on a dialogue line inserts a new element after it and focuses the new row', async () => {
    render(
      <SceneBlock scene={makeScene([{ id: 'el_d', type: 'dialogue', text: 'Line' }])} index={0} />,
    );
    const row = document.querySelector('[data-el-id="el_d"]') as HTMLElement;
    fireEvent.keyDown(row, { key: 'Enter' });

    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({ op: 'insert', after_id: 'el_d' });
    const newId = (ops[0] as Extract<ElementOp, { op: 'insert' }>).element_id;

    await waitFor(() =>
      expect(document.activeElement?.getAttribute('data-el-id')).toBe(newId),
    );
  });

  it('debounces text input into a single update op', () => {
    vi.useFakeTimers();
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: '' }])} index={0} />);
    const row = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    row.textContent = 'New action text';
    fireEvent.input(row);

    expect(sync.dispatch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(500);

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({
      op: 'update',
      element_id: 'el_a',
      payload: { text: 'New action text' },
    });
  });

  it('does not run the machine while an IME composition is active', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    const row = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    fireEvent.compositionStart(row);
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(sync.dispatch).not.toHaveBeenCalled();
  });

  it('splits pasted text on newlines into anchored action inserts', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    const row = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    fireEvent.paste(row, {
      clipboardData: { getData: () => 'Line one\nLine two' },
    });

    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toHaveLength(2);
    expect(ops[0]).toMatchObject({ op: 'insert', after_id: 'el_a', payload: { type: 'action', text: 'Line one' } });
    expect(ops[1]).toMatchObject({ op: 'insert', payload: { type: 'action', text: 'Line two' } });
    // Second insert is anchored after the first (a valid anchor chain).
    expect((ops[1] as Extract<ElementOp, { op: 'insert' }>).after_id).toBe(
      (ops[0] as Extract<ElementOp, { op: 'insert' }>).element_id,
    );
  });

  it('writes scene meta through updateSceneMeta after the debounce', () => {
    vi.useFakeTimers();
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    // Head row starts as a typographic slug — click it to reveal the selects.
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));
    const locationInput = screen.getByLabelText('editor.location');
    fireEvent.change(locationInput, { target: { value: 'Rooftop Access' } });

    expect(svc.updateSceneMeta).not.toHaveBeenCalled();
    vi.advanceTimersByTime(600);

    expect(svc.updateSceneMeta).toHaveBeenCalledWith(
      '900',
      expect.objectContaining({ location_text: 'Rooftop Access' }),
    );
  });
});

describe('SceneBlock typographic head row (Task 4.5)', () => {
  it('renders a read-mode heading slug by default, not the selects', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    // Hollywood slug from INT / Blank Studio / NIGHT.
    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toHaveTextContent(
      'INT. BLANK STUDIO - NIGHT',
    );
    expect(screen.queryByLabelText('editor.location')).toBeNull();
    expect(screen.queryByLabelText('editor.intExt')).toBeNull();
  });

  it('reveals the three selects on click and returns to read mode on blur', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));

    expect(screen.getByLabelText('editor.intExt')).toBeInTheDocument();
    expect(screen.getByLabelText('editor.location')).toBeInTheDocument();
    expect(screen.getByLabelText('editor.timeOfDay')).toBeInTheDocument();

    // Blur to somewhere outside the head row → read mode returns.
    fireEvent.blur(screen.getByLabelText('editor.location'), { relatedTarget: document.body });
    expect(screen.queryByLabelText('editor.location')).toBeNull();
    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toBeInTheDocument();
  });

  it('reflects edited meta in the read-mode slug', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));
    fireEvent.change(screen.getByLabelText('editor.location'), {
      target: { value: 'Rooftop Access' },
    });
    fireEvent.blur(screen.getByLabelText('editor.location'), { relatedTarget: document.body });

    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toHaveTextContent(
      'INT. ROOFTOP ACCESS - NIGHT',
    );
  });
});
