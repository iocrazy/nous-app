import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatInput } from './ChatInput';

/** tiptap renders a div[contenteditable="true"] — ARIA role "textbox" works
 *  with @testing-library/dom >= 9, but jsdom doesn't map implicit ARIA roles
 *  for contenteditable divs reliably. Use querySelector as a safe fallback. */
function getEditorEl(container: HTMLElement): HTMLElement {
  const el = container.querySelector('[contenteditable="true"]') as HTMLElement | null;
  if (!el) throw new Error('tiptap contenteditable not found');
  return el;
}

describe('ChatInput (tiptap)', () => {
  it('renders the editor contenteditable', () => {
    const { container } = render(<ChatInput onSend={() => {}} />);
    const editor = getEditorEl(container);
    expect(editor).toBeDefined();
    expect(editor.getAttribute('contenteditable')).toBe('true');
  });

  it('renders placeholder text', () => {
    render(<ChatInput onSend={() => {}} placeholder="say something" />);
    // tiptap Placeholder extension applies the placeholder via data attribute
    const ph = document.querySelector('[data-placeholder]');
    expect(ph?.getAttribute('data-placeholder')).toBe('say something');
  });

  it('calls onSend with text + empty attachments when nothing referenced', async () => {
    const onSend = vi.fn();
    const editorRef = { current: null } as React.MutableRefObject<import('@tiptap/core').Editor | null>;
    const { container } = render(<ChatInput onSend={onSend} editorRef={editorRef} />);
    // Wait for the editor to mount and populate its ref
    await waitFor(() => {
      expect(editorRef.current).not.toBeNull();
    }, { timeout: 300 });
    // Programmatically set content (bypasses jsdom's contenteditable limitations)
    editorRef.current!.commands.setContent('<p>hello world</p>');
    // Click the Send button
    const sendBtn = screen.getByTitle('Send message');
    fireEvent.click(sendBtn);
    expect(onSend).toHaveBeenCalledOnce();
    const [text, attachments] = onSend.mock.calls[0] as [string, unknown[]];
    expect(text).toBe('hello world');
    expect(Array.isArray(attachments)).toBe(true);
    expect(attachments).toHaveLength(0);
  });

  it('does not call onSend when disabled', async () => {
    const onSend = vi.fn();
    const { container } = render(<ChatInput onSend={onSend} disabled />);
    // When disabled, tiptap sets contenteditable="false" — use a broader selector
    const editor = container.querySelector('[contenteditable]') as HTMLElement | null;
    if (editor) {
      fireEvent.keyDown(editor, { key: 'Enter', code: 'Enter', bubbles: true });
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('renders send button', () => {
    render(<ChatInput onSend={() => {}} />);
    const btn = screen.getByTitle('Send message');
    expect(btn).toBeDefined();
  });
});
