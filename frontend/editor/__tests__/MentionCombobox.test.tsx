import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
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

import { MentionCombobox, filterMentionCandidates } from '../components/MentionCombobox';
import { buildElementHtml } from '../render/layoutShared';
import { HollywoodLayout, type LayoutHandlers } from '../render/HollywoodLayout';
import { MentionNamesContext } from '../render/layoutShared';
import { SceneBlock } from '../components/SceneBlock';
import type { ElementOp, ScriptElement, SceneDoc } from '../types';

afterEach(() => {
  cleanup();
  sync.dispatch.mockReset();
});

// ─── MentionCombobox (presentational listbox; ARIA lives on the focused line) ─

describe('MentionCombobox', () => {
  it('renders a listbox with one option per candidate, ids derived from listboxId', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    // The popup is ONLY a listbox now — the combobox role lives on the line.
    expect(screen.queryByRole('combobox')).toBeNull();
    const listbox = screen.getByRole('listbox');
    expect(listbox).toHaveAttribute('id', 'lb');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveAttribute('id', 'lb-opt-0');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('reflects the controlled activeIndex on aria-selected', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        listboxId="lb"
        activeIndex={1}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'false');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('filters candidates by query, case-insensitively', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query="y"
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(2); // Blythe, Cy
    expect(options.map((o) => o.textContent)).toEqual(['Blythe', 'Cy']);
    // Pure filter helper is exported and shared with SceneBlock.
    expect(filterMentionCandidates(['Ada', 'Blythe', 'Cy'], 'y')).toEqual(['Blythe', 'Cy']);
  });

  it('selects on option click without stealing focus (mousedown default prevented)', () => {
    const onSelect = vi.fn();
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe']}
        query=""
        listboxId="lb"
        activeIndex={0}
        onSelect={onSelect}
        onHover={vi.fn()}
      />,
    );
    fireEvent.mouseDown(screen.getByText('Blythe'));
    expect(onSelect).toHaveBeenCalledWith('Blythe');
  });

  it('shows a no-match hint and renders no options when nothing matches', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe']}
        query="zzz"
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(screen.getByText('editor.mentionNoMatch')).toBeInTheDocument();
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

  it('chips a multi-word KNOWN CAST name as one whole token', () => {
    const html = buildElementHtml('with @John Smith arriving', ['John Smith']);
    expect(html).toContain('data-mention="John Smith"');
    expect(html).toMatch(/class="mh-mention"[^>]*>@John Smith</);
    // The trailing word is plain text, not swallowed into the chip.
    expect(html).toContain(' arriving');
  });

  it('chips only the first word of an UNKNOWN multi-word name', () => {
    const html = buildElementHtml('with @John Smith arriving', []);
    expect(html).toContain('data-mention="John"');
    expect(html).toMatch(/class="mh-mention unknown"[^>]*>@John</);
    // "Smith" stays plain text — no second chip.
    expect(html).not.toContain('data-mention="Smith"');
    expect(html).toContain('Smith arriving');
  });

  it('does not over-match a known name across a word boundary', () => {
    // "John Smith" must NOT chip "@John Smithers" (the boundary char is a letter).
    const html = buildElementHtml('@John Smithers', ['John Smith']);
    expect(html).toContain('data-mention="John"');
    expect(html).not.toContain('data-mention="John Smith"');
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

  it('puts the ARIA combobox on the focused line and points activedescendant at the active option', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
        index={0}
        mentionCandidates={['Ada', 'Blythe']}
      />,
    );
    const line = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    fireEvent.keyDown(line, { key: '@' });
    fireEvent.keyDown(line, { key: 'ArrowDown' });

    // The line (not the popup) is the combobox — AT announces the active option.
    expect(line).toHaveAttribute('role', 'combobox');
    expect(line).toHaveAttribute('aria-expanded', 'true');
    const controls = line.getAttribute('aria-controls');
    expect(controls).toBeTruthy();
    expect(screen.getByRole('listbox')).toHaveAttribute('id', controls!);
    const activeId = line.getAttribute('aria-activedescendant');
    expect(activeId).toBeTruthy();
    const activeOption = document.getElementById(activeId!);
    expect(activeOption).toHaveAttribute('aria-selected', 'true');
    expect(activeOption?.textContent).toBe('Blythe'); // ArrowDown moved to the 2nd
  });

  it('collapses the combobox aria when the filter matches nothing', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
        index={0}
        mentionCandidates={['Ada', 'Blythe']}
      />,
    );
    const line = document.querySelector('[data-el-id="el_a"]') as HTMLElement;
    fireEvent.keyDown(line, { key: '@' });
    // Type a query no candidate matches → the listbox has no options.
    line.textContent = '@zzz';
    fireEvent.input(line);

    // The line stays a combobox but collapses: no dangling controls/descendant.
    expect(line).toHaveAttribute('role', 'combobox');
    expect(line).toHaveAttribute('aria-expanded', 'false');
    expect(line).not.toHaveAttribute('aria-controls');
    expect(line).not.toHaveAttribute('aria-activedescendant');
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

it('quote-bearing mention cannot break out of the data-mention attribute (stored XSS)', () => {
  const html = buildElementHtml('@x"onmouseover="alert(1)', []);
  expect(html).not.toContain('"onmouseover');
  expect(html).toContain('&quot;');
  const host = document.createElement('div');
  host.innerHTML = html;
  const chip = host.querySelector('[data-mention]') as HTMLElement;
  expect(chip.getAttribute('onmouseover')).toBeNull();
  expect(chip.dataset.mention).toBe('x"onmouseover="alert(1)');
});
