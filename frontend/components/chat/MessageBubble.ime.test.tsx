/**
 * MessageBubble.ime.test.tsx — IME composition guard for the edit-save handler.
 *
 * Editing a message and pressing Enter saves it. When an IME composition is
 * committed with Enter (`nativeEvent.isComposing === true`) the edit must NOT
 * be saved mid-composition. jsdom does not forward `isComposing` through
 * fireEvent options, so a native KeyboardEvent is dispatched with the flag set.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-edit-1',
    channel_id: 'chan1',
    seq: '1',
    sender_id: 'U1',
    sender_type: 'user',
    content_type: 'text',
    body: { text: 'original text' },
    reply_to_id: null,
    edited_at: null,
    deleted_at: null,
    created_at: '2024-01-01T12:00:00Z',
    ...overrides,
  };
}

function openEditor(): HTMLTextAreaElement {
  fireEvent.click(screen.getByTitle('chat.edit'));
  return screen.getByRole('textbox') as HTMLTextAreaElement;
}

describe('MessageBubble edit-save IME composition guard', () => {
  it('does NOT save the edit when Enter commits an IME composition', () => {
    const onEdit = vi.fn();
    render(<MessageBubble message={makeMessage()} currentUserId="U1" onEdit={onEdit} />);
    const textarea = openEditor();
    fireEvent.change(textarea, { target: { value: '更新后的文本' } });

    const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
    Object.defineProperty(ev, 'isComposing', { value: true });
    textarea.dispatchEvent(ev);

    expect(onEdit).not.toHaveBeenCalled();
  });

  it('saves on a normal (non-composing) Enter', () => {
    const onEdit = vi.fn();
    render(<MessageBubble message={makeMessage()} currentUserId="U1" onEdit={onEdit} />);
    const textarea = openEditor();
    fireEvent.change(textarea, { target: { value: '更新后的文本' } });

    fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' });

    expect(onEdit).toHaveBeenCalledWith('msg-edit-1', '更新后的文本');
  });
});
