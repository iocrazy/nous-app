import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AsianLayout } from '../render/AsianLayout';
import type { LayoutHandlers } from '../render/HollywoodLayout';
import type { ScriptElement } from '../types';

const noopHandlers = (): LayoutHandlers => ({
  onInput: vi.fn(),
  onKeyDown: vi.fn(),
  onFocus: vi.fn(),
  onPaste: vi.fn(),
  onCompositionStart: vi.fn(),
  onCompositionEnd: vi.fn(),
});

const allTypes: ScriptElement[] = [
  { id: 'el_00000001', type: 'action', text: 'One pool of light drops from above.' },
  { id: 'el_00000002', type: 'character', text: 'Client' },
  { id: 'el_00000003', type: 'dialogue', text: 'I said four seconds.' },
  { id: 'el_00000004', type: 'paren', text: '(quietly)' },
  { id: 'el_00000005', type: 'transition', text: 'CUT TO' },
  { id: 'el_00000006', type: 'comment', text: 'note to self' },
  { id: 'el_00000007', type: 'subtitle', text: 'Six months earlier' },
];

afterEach(cleanup);

describe('AsianLayout', () => {
  it('renders one editable row per element carrying its data-el-id, text and asian class', () => {
    const { container } = render(
      <AsianLayout elements={allTypes} focusedElementId={null} handlers={noopHandlers()} />,
    );

    const rows = container.querySelectorAll('[data-el-id]');
    expect(rows).toHaveLength(7);

    expect(container.querySelector('[data-el-id="el_00000001"]')).toHaveClass('as-action');
    expect(container.querySelector('[data-el-id="el_00000002"]')).toHaveClass('as-character');
    expect(container.querySelector('[data-el-id="el_00000003"]')).toHaveClass('as-dialogue');
    expect(container.querySelector('[data-el-id="el_00000004"]')).toHaveClass('as-paren');
    expect(container.querySelector('[data-el-id="el_00000005"]')).toHaveClass('as-transition');
    expect(container.querySelector('[data-el-id="el_00000006"]')).toHaveClass('as-comment');
    expect(container.querySelector('[data-el-id="el_00000007"]')).toHaveClass('as-subtitle');

    const action = container.querySelector('[data-el-id="el_00000001"]')!;
    expect(action.textContent).toBe('One pool of light drops from above.');
  });

  it('prefixes action rows with an aria-hidden △ marker', () => {
    const { container } = render(
      <AsianLayout
        elements={[allTypes[0]]}
        focusedElementId={null}
        handlers={noopHandlers()}
      />,
    );
    const mark = container.querySelector('.as-prefix')!;
    expect(mark).toBeInTheDocument();
    expect(mark.textContent).toBe('△');
    expect(mark).toHaveAttribute('aria-hidden', 'true');
  });

  it('gives character rows a trailing colon label marker (left-aligned, not centered)', () => {
    const { container } = render(
      <AsianLayout
        elements={[allTypes[1]]}
        focusedElementId={null}
        handlers={noopHandlers()}
      />,
    );
    const colon = container.querySelector('.as-suffix')!;
    expect(colon).toBeInTheDocument();
    expect(colon.textContent).toBe(':');
    expect(colon).toHaveAttribute('aria-hidden', 'true');
    // The character cue itself is left-aligned label styling, distinct from
    // Hollywood's centered cue — asserted via the as-character class.
    expect(container.querySelector('[data-el-id="el_00000002"]')).toHaveClass('as-character');
  });

  it('marks dialogue rows as indented under the character label', () => {
    const { container } = render(
      <AsianLayout
        elements={[allTypes[2]]}
        focusedElementId={null}
        handlers={noopHandlers()}
      />,
    );
    expect(container.querySelector('.as-row-dialogue')).toBeInTheDocument();
  });

  it('does NOT emit the △ / colon markers for non-action / non-character rows', () => {
    const { container } = render(
      <AsianLayout
        elements={[allTypes[2], allTypes[3], allTypes[6]]}
        focusedElementId={null}
        handlers={noopHandlers()}
      />,
    );
    expect(container.querySelector('.as-prefix')).toBeNull();
    expect(container.querySelector('.as-suffix')).toBeNull();
  });

  it('applies the focused class only to the focused row', () => {
    const { container } = render(
      <AsianLayout
        elements={allTypes}
        focusedElementId="el_00000002"
        handlers={noopHandlers()}
      />,
    );
    const focused = container.querySelectorAll('.mh-el-row.focused');
    expect(focused).toHaveLength(1);
    expect(focused[0].querySelector('[data-el-id="el_00000002"]')).toBeInTheDocument();
  });
});
