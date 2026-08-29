// The prompt body editor: a tiptap surface that can hold inline image chips.
//
// Why tiptap and not the textarea it replaces: a chip is a DOM element, and a
// textarea holds only characters. The chip is not decoration — it IS the
// reference image (see promptImageRefs.ts), so it has to live in the document.
//
// The React Flow interop assertions are not incidental. A canvas node's editor
// sits inside a draggable, zoomable surface; `nodrag`/`nowheel` must land on
// the contenteditable element itself, because that is the element React Flow
// reads them from. On a wrapper they silently do nothing.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createRef } from 'react';

import { PromptBodyEditor, type PromptBodyEditorHandle } from './PromptBodyEditor';

afterEach(cleanup);

const CHIP = { url: '/api/v1/generated-media/5/cover', alias: 'hero.png', kind: 'image' };

describe('PromptBodyEditor', () => {
  it('shows the initial text', async () => {
    render(<PromptBodyEditor value="a wide shot" onChange={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId('prompt-body-editor')).toHaveTextContent('a wide shot'),
    );
  });

  it('puts nodrag and nowheel on the editable element itself', async () => {
    render(<PromptBodyEditor value="" onChange={() => {}} />);
    const el = await screen.findByTestId('prompt-body-editor');
    expect(el.getAttribute('contenteditable')).toBe('true');
    expect(el.classList.contains('nodrag')).toBe(true);
    expect(el.classList.contains('nowheel')).toBe(true);
  });

  it('renders an image chip with its thumbnail and alias', async () => {
    render(<PromptBodyEditor value="" onChange={() => {}} initialChips={[CHIP]} />);
    const chip = await screen.findByTestId('prompt-image-chip');
    expect(chip).toHaveTextContent('hero.png');
    // The src goes through mediaSrc(), so it is no longer the bare relative
    // path — asserting equality here is what let the broken-image bug ship.
    // What matters is that it still points at this resource.
    const src = chip.querySelector('img')?.getAttribute('src') ?? '';
    expect(src).toContain('/generated-media/5/cover');
    expect(src.startsWith('/'), 'relative src will 404 across origins').toBe(false);
  });

  it('reports a chip as @alias in the prompt text', async () => {
    const onChange = vi.fn();
    render(<PromptBodyEditor value="" onChange={onChange} initialChips={[CHIP]} />);
    await screen.findByTestId('prompt-image-chip');
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(expect.stringContaining('@hero.png')),
    );
  });

  it('reports the chip in the ref list', async () => {
    const onRefsChange = vi.fn();
    render(
      <PromptBodyEditor value="" onChange={() => {}} onRefsChange={onRefsChange} initialChips={[CHIP]} />,
    );
    await screen.findByTestId('prompt-image-chip');
    await waitFor(() =>
      expect(onRefsChange).toHaveBeenLastCalledWith([
        expect.objectContaining({ url: CHIP.url, alias: 'hero.png' }),
      ]),
    );
  });

  it('removing a chip drops it from the reported refs', async () => {
    const onRefsChange = vi.fn();
    render(
      <PromptBodyEditor value="" onChange={() => {}} onRefsChange={onRefsChange} initialChips={[CHIP]} />,
    );
    const remove = await screen.findByRole('button', { name: /remove hero\.png/i });
    fireEvent.click(remove);
    await waitFor(() => expect(onRefsChange).toHaveBeenLastCalledWith([]));
  });

  it('signals when @ is typed so the caller can open the picker', async () => {
    const onAtTyped = vi.fn();
    render(<PromptBodyEditor value="" onChange={() => {}} onAtTyped={onAtTyped} />);
    const el = await screen.findByTestId('prompt-body-editor');
    fireEvent.keyDown(el, { key: '@' });
    await waitFor(() => expect(onAtTyped).toHaveBeenCalled());
  });

  it('is not editable when read-only', async () => {
    render(<PromptBodyEditor value="locked" onChange={() => {}} readOnly />);
    const el = await screen.findByTestId('prompt-body-editor');
    expect(el.getAttribute('contenteditable')).toBe('false');
  });

  it('stays focusable and readable when read-only, so it can be copied', async () => {
    // A contenteditable=false div is not focusable by default. Without an
    // explicit tabindex a viewer cannot tab to the prompt or select its text
    // — the same regression the textarea version guarded against by using
    // `readonly` rather than `disabled`.
    render(<PromptBodyEditor value="a locked prompt" onChange={() => {}} readOnly />);
    const el = await screen.findByTestId('prompt-body-editor');
    expect(el).toHaveAttribute('tabindex', '0');
    expect(el).toHaveAttribute('aria-readonly', 'true');
    el.focus();
    expect(document.activeElement).toBe(el);
    expect(el.textContent).toContain('a locked prompt');
  });

  it('accepts a chip inserted through the imperative handle', async () => {
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={() => {}} />);
    await screen.findByTestId('prompt-body-editor');
    expect(screen.queryByTestId('prompt-image-chip')).toBeNull();
    ref.current?.insertImage(CHIP);
    const chip = await screen.findByTestId('prompt-image-chip');
    expect(chip).toHaveTextContent('hero.png');
  });
});

// The 2026-08-18 incident contract, verified at the component that now owns
// it. Typing Chinese used to let raw pinyin reach the store mid-composition;
// the async round-trip then rewrote the controlled value and the IME
// committed letters as text. Nothing may leave this component while an input
// method is mid-composition.
describe('PromptBodyEditor — IME composition guard', () => {
  function compose(el: HTMLElement, fn: () => void): void {
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    fn();
    el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
  }

  it('does not report text while composing', async () => {
    const onChange = vi.fn();
    render(<PromptBodyEditor value="" onChange={onChange} />);
    const el = await screen.findByTestId('prompt-body-editor');
    onChange.mockClear();

    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    const p = el.querySelector('p')!;
    p.textContent = 'zhe';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 20));

    expect(onChange, 'pinyin reached the store mid-composition').not.toHaveBeenCalled();
  });

  it('reports the committed text once composition ends', async () => {
    const onChange = vi.fn();
    render(<PromptBodyEditor value="" onChange={onChange} />);
    const el = await screen.findByTestId('prompt-body-editor');
    onChange.mockClear();

    compose(el, () => {
      const p = el.querySelector('p')!;
      p.textContent = '这是';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('这是'));
  });

  it('an @ typed by an IME does not open the picker', async () => {
    const onAtTyped = vi.fn();
    render(<PromptBodyEditor value="" onChange={() => {}} onAtTyped={onAtTyped} />);
    const el = await screen.findByTestId('prompt-body-editor');

    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    fireEvent.keyDown(el, { key: '@' });
    await new Promise((r) => setTimeout(r, 20));

    expect(onAtTyped, 'picker opened during composition').not.toHaveBeenCalled();
  });
});
