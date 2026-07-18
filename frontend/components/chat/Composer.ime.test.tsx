/**
 * Composer.ime.test.tsx — IME composition guard for the chat send handler.
 *
 * A Chinese/Japanese/Korean IME commits a composition with an Enter keypress.
 * That Enter carries `nativeEvent.isComposing === true`; without a guard it
 * would send the half-composed message (or, with the @mention dropdown open,
 * hijack the composition into a candidate pick). jsdom does not forward
 * `isComposing` through fireEvent options, so we dispatch a native
 * KeyboardEvent with the flag defined (same fallback as
 * EagleTagBrowser.enter.test.tsx).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Composer } from './Composer';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const MEMBERS = [
  { user_id: 'U1', label: 'Alice' },
  { user_id: 'U2', label: 'Alice Smith' },
];
const AGENTS = [{ slug: 'bot', label: 'Bot' }];

function typeInto(textarea: HTMLTextAreaElement, value: string): void {
  textarea.value = value;
  textarea.selectionStart = value.length;
  textarea.selectionEnd = value.length;
  fireEvent.input(textarea);
}

function pressEnterComposing(textarea: HTMLTextAreaElement): void {
  const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
  Object.defineProperty(ev, 'isComposing', { value: true });
  textarea.dispatchEvent(ev);
}

function pressEnter(textarea: HTMLTextAreaElement): void {
  fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter', bubbles: true });
}

describe('Composer IME composition guard', () => {
  let onSend: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onSend = vi.fn();
  });

  it('does NOT send when Enter commits an IME composition (dropdown closed)', () => {
    render(<Composer onSend={onSend} members={MEMBERS} agents={AGENTS} />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    textarea.value = '你好'; // set directly → no input event → dropdown stays closed
    pressEnterComposing(textarea);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('sends on a normal (non-composing) Enter after composition ends', () => {
    render(<Composer onSend={onSend} members={MEMBERS} agents={AGENTS} />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    textarea.value = '你好';
    pressEnter(textarea);
    expect(onSend).toHaveBeenCalledOnce();
    const [text] = onSend.mock.calls[0] as [string, string[]];
    expect(text).toBe('你好');
  });

  it('does NOT pick a mention candidate when Enter commits a composition (dropdown open)', () => {
    render(<Composer onSend={onSend} members={MEMBERS} agents={AGENTS} />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '@Al'); // opens the dropdown, Alice is the active candidate
    expect(screen.getByText('Alice')).toBeTruthy();
    pressEnterComposing(textarea);
    // Composition Enter must fall through to the IME, not insert the token.
    expect(textarea.value).not.toContain('@Alice');
    expect(onSend).not.toHaveBeenCalled();
  });
});
