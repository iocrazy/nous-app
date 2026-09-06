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

  it('reopens a saved body with the chip back where the user put it', async () => {
    // Reopening a canvas re-mounts this editor from what was persisted: the
    // plain-text body (chips already flattened to `@alias`) plus `image_refs`.
    // Before the fix the alias stayed as literal text and the chip was
    // appended, so `@Image 1 扩图` came back as `@Image 1 扩图 [chip]`.
    const onChange = vi.fn();
    render(
      <PromptBodyEditor
        value="@Image 1 扩图，旁边放老虎"
        onChange={onChange}
        initialChips={[{ ...CHIP, alias: 'Image 1' }]}
      />,
    );
    await screen.findByTestId('prompt-image-chip');
    const el = screen.getByTestId('prompt-body-editor');

    // One chip, and the literal token is no longer sitting in the prose.
    expect(screen.getAllByTestId('prompt-image-chip')).toHaveLength(1);
    expect(el.textContent).not.toContain('@Image 1');

    // The chip leads the line, exactly as it was saved.
    const chip = screen.getByTestId('prompt-image-chip');
    const para = el.querySelector('p')!;
    expect(para.firstElementChild?.contains(chip)).toBe(true);

    // And the text the parent gets back is unchanged — a reopen must not
    // rewrite the user's prompt.
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('@Image 1 扩图，旁边放老虎'),
    );
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

  // The insert's `@`-consuming step is the `@` PICKER's contract — the user
  // typed `@que`, so the chip must replace that token. It is implemented as
  // "delete from the last literal `@` within 80 characters back to the
  // caret", which cannot tell a pending query from prose. A caller with no
  // pending query (the ⌥ library drop) must be able to opt out, or it eats
  // whatever the user happened to write.
  it('the DEFAULT still consumes a pending @query — the picker depends on it', async () => {
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');
    // Typed through the handle so the caret ends up AFTER the text, which is
    // where a person's caret is when they pick from the picker.
    ref.current?.insertText('a wide shot @que');

    ref.current?.insertImage(CHIP);

    await screen.findByTestId('prompt-image-chip');
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('a wide shot @hero.png '),
    );
  });

  it('consumeMention:false leaves an @ in the prose alone', async () => {
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');
    ref.current?.insertText('contact me at foo@bar.com');

    ref.current?.insertImage(CHIP, { consumeMention: false });

    await screen.findByTestId('prompt-image-chip');
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('contact me at foo@bar.com@hero.png '),
    );
  });

  // Ruling R16: `insertText` takes the same opt-out the chip inserters do.
  // Its contract used to be consume-only, and the mention-handle registry
  // documented the opposite — a caller reading that comment would have skipped
  // the guard and shipped the `@bar.com` bug in plain-text form.
  it('insertText with consumeMention:false leaves a pending @ standing', async () => {
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');
    ref.current?.insertText('draft @');

    ref.current?.insertText('hero', { consumeMention: false });

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('draft @hero'));
  });

  it('insertText DEFAULTS to consuming, unchanged — the picker contract', async () => {
    // Pinned, not chosen: every existing caller passes no options, so the
    // default must stay exactly what it was before R16 widened the signature.
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');
    ref.current?.insertText('draft @');

    ref.current?.insertText('hero');

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('draft hero'));
  });

  it('consumeMention:false on a never-focused editor appends — not position 0', async () => {
    // The Library panel and the ⌥ drop insert into a card nobody is typing
    // in. ProseMirror's seed selection on such a card is the document start,
    // so the chip used to land in FRONT of the prose. No focus → no caret
    // intent → append.
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="a wide shot" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');

    ref.current?.insertImage(CHIP, { consumeMention: false });

    await screen.findByTestId('prompt-image-chip');
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('a wide shot@hero.png '),
    );
  });

  it('consumeMention:false survives a SECOND @ in range — chip one is not eaten', async () => {
    // The narrow multi-item case: with two literal `@` inside the 80-character
    // window, the default deleted back past the first chip. Two drops in a row
    // must leave both chips standing.
    const onChange = vi.fn();
    const ref = createRef<PromptBodyEditorHandle>();
    render(<PromptBodyEditor ref={ref} value="" onChange={onChange} />);
    await screen.findByTestId('prompt-body-editor');
    ref.current?.insertText('a@b and c@d');

    ref.current?.insertImage(CHIP, { consumeMention: false });
    ref.current?.insertImage({ ...CHIP, url: '/api/v1/generated-media/6/cover', alias: 'two.png' }, {
      consumeMention: false,
    });

    await waitFor(() => expect(screen.getAllByTestId('prompt-image-chip')).toHaveLength(2));
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('a@b and c@d@hero.png @two.png '),
    );
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
