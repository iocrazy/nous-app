/**
 * ChatInput.ime.test.tsx — IME composition guard for the tiptap send handler.
 *
 * ChatInput's send lives in ProseMirror's `handleKeyDown(view, event)`, where
 * `event` is a raw DOM KeyboardEvent — so the guard reads `event.isComposing`
 * (not `.nativeEvent.isComposing`). An Enter that commits an IME composition
 * must not send. Content is set via the editor ref (jsdom can't type into a
 * contenteditable); the keydown is dispatched natively on the editor DOM so the
 * `isComposing` flag reaches ProseMirror's handler.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { ChatInput } from './ChatInput';
import type { Editor } from '@tiptap/core';

function getEditorEl(container: HTMLElement): HTMLElement {
  const el = container.querySelector('[contenteditable="true"]') as HTMLElement | null;
  if (!el) throw new Error('tiptap contenteditable not found');
  return el;
}

function dispatchEnter(el: HTMLElement, composing: boolean): void {
  const ev = new KeyboardEvent('keydown', {
    key: 'Enter',
    code: 'Enter',
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(ev, 'isComposing', { value: composing });
  el.dispatchEvent(ev);
}

async function renderWithContent(onSend: () => void) {
  const editorRef = { current: null } as React.MutableRefObject<Editor | null>;
  const result = render(<ChatInput onSend={onSend} editorRef={editorRef} />);
  await waitFor(() => expect(editorRef.current).not.toBeNull(), { timeout: 300 });
  editorRef.current!.commands.setContent('<p>你好世界</p>');
  return result;
}

describe('ChatInput IME composition guard', () => {
  it('sends on a normal (non-composing) Enter', async () => {
    const onSend = vi.fn();
    const { container } = await renderWithContent(onSend);
    dispatchEnter(getEditorEl(container), false);
    expect(onSend).toHaveBeenCalledOnce();
    const [text] = onSend.mock.calls[0] as [string, unknown[]];
    expect(text).toBe('你好世界');
  });

  it('does NOT send when Enter commits an IME composition', async () => {
    const onSend = vi.fn();
    const { container } = await renderWithContent(onSend);
    dispatchEnter(getEditorEl(container), true);
    expect(onSend).not.toHaveBeenCalled();
  });
});
