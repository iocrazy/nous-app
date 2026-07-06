import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HollywoodLayout, type LayoutHandlers } from '../render/HollywoodLayout';
import type { ScriptElement } from '../types';

const noopHandlers = (): LayoutHandlers => ({
  onInput: vi.fn(),
  onKeyDown: vi.fn(),
  onFocus: vi.fn(),
  onPaste: vi.fn(),
  onCompositionStart: vi.fn(),
  onCompositionEnd: vi.fn(),
});

const elements: ScriptElement[] = [
  { id: 'el_00000001', type: 'action', text: 'One pool of light drops from above.' },
  { id: 'el_00000002', type: 'character', text: 'Client' },
  { id: 'el_00000003', type: 'dialogue', text: 'I said four seconds.' },
];

afterEach(cleanup);

describe('HollywoodLayout', () => {
  it('renders one editable row per element with its data-el-id, Hollywood class and text', () => {
    const { container } = render(
      <HollywoodLayout elements={elements} focusedElementId={null} handlers={noopHandlers()} />,
    );

    const rows = container.querySelectorAll('[data-el-id]');
    expect(rows).toHaveLength(3);

    const action = container.querySelector('[data-el-id="el_00000001"]')!;
    expect(action).toHaveClass('hw-action');
    expect(action.textContent).toBe('One pool of light drops from above.');

    const character = container.querySelector('[data-el-id="el_00000002"]')!;
    expect(character).toHaveClass('hw-character');
    expect(character).toHaveAttribute('data-el-type', 'character');

    const dialogue = container.querySelector('[data-el-id="el_00000003"]')!;
    expect(dialogue).toHaveClass('hw-dialogue');
  });

  it('marks each row with a type-coloured gutter tick', () => {
    const { container } = render(
      <HollywoodLayout elements={elements} focusedElementId={null} handlers={noopHandlers()} />,
    );
    expect(container.querySelector('.mh-el-tick.t-action')).toBeInTheDocument();
    expect(container.querySelector('.mh-el-tick.t-character')).toBeInTheDocument();
    expect(container.querySelector('.mh-el-tick.t-dialogue')).toBeInTheDocument();
  });

  it('applies the focused class only to the focused row', () => {
    const { container } = render(
      <HollywoodLayout
        elements={elements}
        focusedElementId="el_00000002"
        handlers={noopHandlers()}
      />,
    );
    const focusedRow = container.querySelector('[data-el-id="el_00000002"]')!.closest('.mh-el-row');
    expect(focusedRow).toHaveClass('focused');
    const otherRow = container.querySelector('[data-el-id="el_00000001"]')!.closest('.mh-el-row');
    expect(otherRow).not.toHaveClass('focused');
  });
});
