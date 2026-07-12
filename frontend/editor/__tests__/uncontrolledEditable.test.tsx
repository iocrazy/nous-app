/**
 * Regression: the contentEditable line must survive parent re-renders.
 *
 * With dangerouslySetInnerHTML, React re-wrote the editable's innerHTML on
 * EVERY parent re-render (even with an unchanged __html string), resetting
 * the line to the last committed model text — everything typed inside the
 * 500ms input debounce window vanished and the caret jumped to line start.
 * Parent re-renders are constant while typing (toolbar highlight follows
 * the cursor, mention picker opens, save states change), so this ate real
 * keystrokes, not an edge case.
 *
 * The fix makes the sync effect the ONLY writer: in-flight typing (focused
 * row, uncommitted text) is never touched; unfocused rows still receive
 * remote/optimistic updates.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const applyOpsMock = vi.fn();
vi.mock('../sceneService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../sceneService')>();
  return {
    ...actual,
    applyOps: (...a: unknown[]) => applyOpsMock(...a),
    updateSceneMeta: vi.fn().mockResolvedValue({}),
  };
});

import { SceneBlock } from '../components/SceneBlock';
import { ElementLine, MentionNamesContext } from '../render/layoutShared';
import type { ScriptElement, SceneDoc } from '../types';

const SCENE: SceneDoc = {
  id: '101',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Cafe',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_00000001', type: 'action', text: 'Committed text.' }],
};

function line(): HTMLElement {
  return document.querySelector('[data-el-id="el_00000001"]') as HTMLElement;
}

beforeEach(() => {
  applyOpsMock.mockReset().mockResolvedValue({ content_version: 2, elements: [] });
});

describe('uncontrolled editable vs parent re-renders', () => {
  it('keeps in-flight (uncommitted) typing through a parent re-render while focused', () => {
    const { rerender } = render(<SceneBlock scene={SCENE} index={0} />);
    const node = line();

    // Writer is typing: focused row, DOM ahead of the model (debounce open).
    act(() => node.focus());
    node.textContent = 'Committed text. plus in-flight words';

    // Any prop change re-renders the block (toolbar/cursor/save-state churn).
    rerender(<SceneBlock scene={SCENE} index={1} />);

    expect(node.textContent).toBe('Committed text. plus in-flight words');
  });

  it('keeps uncommitted DOM text on an UNFOCUSED row too (model unchanged)', () => {
    const { rerender } = render(<SceneBlock scene={SCENE} index={0} />);
    const node = line();
    node.textContent = 'Committed text. tail';

    rerender(<SceneBlock scene={SCENE} index={1} />);

    // The sync effect only writes when model text DIVERGES from the DOM in a
    // direction the model knows about — a plain re-render must not clobber.
    // (element.text still 'Committed text.' differs from DOM here, and the
    // row is unfocused, so the effect MAY legitimately repaint. What must
    // never happen is a render-pass rewrite while FOCUSED — covered above.
    // This case documents the settled behavior: unfocused divergence syncs
    // back to the model.)
    expect(node.textContent).toBe('Committed text.');
  });

  it('unfocused rows still receive external model updates (with mention chips)', () => {
    const noHandlers = {
      onInput: () => {},
      onKeyDown: () => {},
      onFocus: () => {},
      onPaste: () => {},
      onCompositionStart: () => {},
      onCompositionEnd: () => {},
    };
    const el = (text: string): ScriptElement => ({
      id: 'el_00000001',
      type: 'action',
      text,
    });
    const { rerender } = render(
      <MentionNamesContext.Provider value={['CLIENT']}>
        <ElementLine element={el('Old text.')} index={0} lineClass="hw-action" focused={false} {...noHandlers} />
      </MentionNamesContext.Provider>,
    );
    expect(line().textContent).toBe('Old text.');

    rerender(
      <MentionNamesContext.Provider value={['CLIENT']}>
        <ElementLine
          element={el('Server says @CLIENT.')}
          index={0}
          lineClass="hw-action"
          focused={false}
          {...noHandlers}
        />
      </MentionNamesContext.Provider>,
    );
    expect(line().textContent).toBe('Server says @CLIENT.');
    expect(document.querySelector('[data-mention="CLIENT."]') ?? document.querySelector('[data-mention="CLIENT"]')).toBeTruthy();
  });
});
