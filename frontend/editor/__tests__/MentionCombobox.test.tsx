import { createRef } from 'react';
import { render, screen, cleanup, fireEvent, act, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// i18n: echo the key so assertions are language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// SceneBlock's collaborators — stateful sync so optimistic edits render, and a
// deterministic id/meta service.
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

const sync = vi.hoisted(() => ({ dispatch: vi.fn() }));
vi.mock('../useSceneSync', async () => {
  const React = await import('react');
  return {
    useSceneSync: (scene: { elements: unknown[]; content_version: number }) => {
      const [elements, setElements] = React.useState(scene.elements);
      return {
        elements,
        version: scene.content_version,
        saveState: 'saved' as const,
        conflict: null,
        dispatchOps: (ops: unknown, optimistic: unknown[]) => {
          sync.dispatch(ops, optimistic);
          setElements(optimistic);
        },
        resolveConflict: () => {},
        flush: async () => {},
      };
    },
  };
});

import { MentionCombobox, type MentionComboboxHandle } from '../components/MentionCombobox';
import { buildElementHtml } from '../render/layoutShared';
import { HollywoodLayout, type LayoutHandlers } from '../render/HollywoodLayout';
import { MentionNamesContext } from '../render/layoutShared';
import { SceneBlock } from '../components/SceneBlock';
import type { ElementOp, ScriptElement, SceneDoc } from '../types';

afterEach(() => {
  cleanup();
  sync.dispatch.mockReset();
});

// ─── MentionCombobox (presentational + keyboard handle) ──────────────────────

describe('MentionCombobox', () => {
  it('renders an ARIA combobox/listbox with one option per candidate', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const combobox = screen.getByRole('combobox');
    expect(combobox).toHaveAttribute('aria-expanded', 'true');
    expect(combobox).toHaveAttribute('aria-haspopup', 'listbox');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('filters candidates by query, case-insensitively', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query="y"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(2); // Blythe, Cy
    expect(options.map((o) => o.textContent)).toEqual(['Blythe', 'Cy']);
  });

  it('navigates with the imperative handle and confirms the active candidate', () => {
    const onSelect = vi.fn();
    const ref = createRef<MentionComboboxHandle>();
    render(
      <MentionCombobox
        ref={ref}
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    // Starts on the first candidate; one move down lands on the second.
    act(() => ref.current!.move(1));
    act(() => {
      ref.current!.confirm();
    });
    expect(onSelect).toHaveBeenCalledWith('Blythe');
  });

  it('selects on option click without stealing focus (mousedown default prevented)', () => {
    const onSelect = vi.fn();
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe']}
        query=""
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    fireEvent.mouseDown(screen.getByText('Blythe'));
    expect(onSelect).toHaveBeenCalledWith('Blythe');
  });

  it('shows a no-match hint and confirm is a no-op when nothing matches', () => {
    const onSelect = vi.fn();
    const ref = createRef<MentionComboboxHandle>();
    render(
      <MentionCombobox
        ref={ref}
        candidates={['Ada', 'Blythe']}
        query="zzz"
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(screen.getByText('editor.mentionNoMatch')).toBeInTheDocument();
    act(() => {
      expect(ref.current!.confirm()).toBe(false);
    });
    expect(onSelect).not.toHaveBeenCalled();
  });
});

// ─── Inline mention chip rendering (layoutShared) ────────────────────────────

describe('mention chips', () => {
  it('wraps a known @name as a highlighted chip and an unknown one as a fallback', () => {
    const html = buildElementHtml('Hi @Client and @Ghost', ['Client']);
    expect(html).toContain('data-mention="Client"');
    expect(html).toContain('data-mention="Ghost"');
    // Known chip has no "unknown" modifier; the unknown one does.
    expect(html).toMatch(/class="mh-mention"[^>]*>@Client/);
    expect(html).toMatch(/class="mh-mention unknown"[^>]*>@Ghost/);
  });

  it('escapes surrounding text and leaves plain lines untouched', () => {
    expect(buildElementHtml('a < b', [])).toBe('a &lt; b');
    expect(buildElementHtml('', [])).toBe('');
  });

  it('renders the chip span in the editable row through the layout engine', () => {
    const noop: LayoutHandlers = {
      onInput: vi.fn(),
      onKeyDown: vi.fn(),
      onFocus: vi.fn(),
      onPaste: vi.fn(),
      onCompositionStart: vi.fn(),
      onCompositionEnd: vi.fn(),
    };
    const elements: ScriptElement[] = [
      { id: 'el_x', type: 'action', text: 'Enter @Ghost quietly' },
    ];
    const { container } = render(
      <MentionNamesContext.Provider value={['Client']}>
        <HollywoodLayout elements={elements} focusedElementId={null} handlers={noop} />
      </MentionNamesContext.Provider>,
    );
    const chip = container.querySelector('[data-mention="Ghost"]')!;
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveClass('unknown');
  });
});

// ─── SceneBlock @ trigger + character-row selector ───────────────────────────

const makeScene = (elements: ScriptElement[]): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements,
});

describe('SceneBlock @ mention integration', () => {
  it('opens the combobox when @ is typed and inserts @Name on select', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
        index={0}
        mentionCandidates={['Ada', 'Blythe']}
      />,
    );
    const line = screen.getByText('A');
    fireEvent.keyDown(line, { key: '@' });
    expect(screen.getByTestId('mention-combobox')).toBeInTheDocument();

    fireEvent.keyDown(line, { key: 'ArrowDown' });
    fireEvent.keyDown(line, { key: 'Enter' });

    // The optimistic update op carries the inserted @Blythe token.
    const calls = sync.dispatch.mock.calls;
    const last = calls[calls.length - 1];
    const ops = last[0] as ElementOp[];
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_a' });
    const payload = (ops[0] as { payload: { text?: string } }).payload;
    expect(payload.text).toContain('@Blythe');
    // Combobox closed after selection.
    expect(screen.queryByTestId('mention-combobox')).toBeNull();
  });

  it('closes on Escape with no dispatched op', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
        index={0}
        mentionCandidates={['Ada']}
      />,
    );
    const line = screen.getByText('A');
    fireEvent.keyDown(line, { key: '@' });
    expect(screen.getByTestId('mention-combobox')).toBeInTheDocument();

    fireEvent.keyDown(line, { key: 'Escape' });
    expect(screen.queryByTestId('mention-combobox')).toBeNull();
    expect(sync.dispatch).not.toHaveBeenCalled();
  });

  it('opens the same selector when a character row gains focus, and Tab closes it back to action', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_c', type: 'character', text: '' }])}
        index={0}
        mentionCandidates={['Ada', 'Blythe']}
      />,
    );
    const line = document.querySelector('[data-el-id="el_c"]') as HTMLElement;
    fireEvent.focus(line);
    const pop = screen.getByTestId('mention-combobox');
    expect(pop).toBeInTheDocument();
    expect(within(pop).getAllByRole('option')).toHaveLength(2);

    fireEvent.keyDown(line, { key: 'Tab' });
    // Combobox closed and the row was retyped to action.
    expect(screen.queryByTestId('mention-combobox')).toBeNull();
    const calls = sync.dispatch.mock.calls;
    const last = calls[calls.length - 1];
    const ops = last[0] as ElementOp[];
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_c', payload: { type: 'action' } });
  });
});
