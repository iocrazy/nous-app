/**
 * MessageBubble.test.tsx — RTL tests for rendering and owner-gate logic.
 *
 * i18n: react-i18next is mocked so that t(key) returns the key itself.
 * Assertions use i18n keys (e.g. 'chat.deleted', 'chat.edit') not English strings.
 *
 * The edit/delete action cluster has CSS opacity-0 but is always in the DOM
 * when isOwn && !isDeleted — jsdom ignores CSS, so getByTitle works directly.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from '../../types';

// Mock react-i18next: t(key) → key, so tests assert on i18n keys not English.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string) => k,
  }),
}));

// ── Fixture factory ───────────────────────────────────────────────────────────

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg1',
    channel_id: 'chan1',
    seq: '1',
    sender_id: 'U2',
    sender_type: 'user',
    content_type: 'text',
    body: { text: 'Hello world' },
    reply_to_id: null,
    edited_at: null,
    deleted_at: null,
    created_at: '2024-01-01T12:00:00Z',
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('MessageBubble', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // ── Rendering ──────────────────────────────────────────────────────────────

  it('renders body.text for a plain text message', () => {
    render(
      <MessageBubble
        message={makeMessage({ body: { text: 'Hello world' } })}
        currentUserId="U1"
      />,
    );
    expect(screen.getByText('Hello world')).toBeDefined();
  });

  it('shows the AGENT tag when sender_type is agent', () => {
    render(
      <MessageBubble
        message={makeMessage({ sender_type: 'agent', sender_id: 'bot-id' })}
        currentUserId="U1"
      />,
    );
    expect(screen.getByText('AGENT')).toBeDefined();
  });

  it('renders media card title and a field', () => {
    const body = {
      title: 'My Video',
      fields: [{ title: 'Duration', value: '5:00' }],
    };
    render(
      <MessageBubble
        message={makeMessage({ content_type: 'media_card', body })}
        currentUserId="U1"
      />,
    );
    expect(screen.getByText('My Video')).toBeDefined();
    expect(screen.getByText('Duration')).toBeDefined();
    expect(screen.getByText('5:00')).toBeDefined();
  });

  // ── Deleted message ────────────────────────────────────────────────────────

  it('renders tombstone (chat.deleted key) for deleted messages', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          deleted_at: '2024-01-01T12:01:00Z',
          body: { text: 'secret text' },
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText('chat.deleted')).toBeDefined();
  });

  it('hides original body text when deleted', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          deleted_at: '2024-01-01T12:01:00Z',
          body: { text: 'secret text' },
        })}
        currentUserId="U1"
      />,
    );
    expect(screen.queryByText('secret text')).toBeNull();
  });

  it('hides edit and delete action buttons when deleted (even for own message)', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          deleted_at: '2024-01-01T12:01:00Z',
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByTitle('chat.edit')).toBeNull();
    expect(screen.queryByTitle('chat.delete')).toBeNull();
  });

  // ── Edited marker ──────────────────────────────────────────────────────────

  it('shows the edited marker (chat.edited key) when edited_at is set and not deleted', () => {
    render(
      <MessageBubble
        message={makeMessage({
          edited_at: '2024-01-01T12:01:00Z',
        })}
        currentUserId="U1"
      />,
    );
    // The marker is rendered inside a span as '· chat.edited' (the key is embedded)
    expect(screen.getByText(/chat\.edited/)).toBeDefined();
  });

  it('does NOT show edited marker when the message is deleted', () => {
    render(
      <MessageBubble
        message={makeMessage({
          edited_at: '2024-01-01T12:01:00Z',
          deleted_at: '2024-01-01T12:02:00Z',
        })}
        currentUserId="U1"
      />,
    );
    expect(screen.queryByText(/chat\.edited/)).toBeNull();
  });

  // ── Owner gate ─────────────────────────────────────────────────────────────

  it('shows edit AND delete buttons for own text message', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          content_type: 'text',
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByTitle('chat.edit')).toBeDefined();
    expect(screen.getByTitle('chat.delete')).toBeDefined();
  });

  it('shows NO action buttons for a non-own message', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U2',
          sender_type: 'user',
          content_type: 'text',
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByTitle('chat.edit')).toBeNull();
    expect(screen.queryByTitle('chat.delete')).toBeNull();
  });

  it('own media_card message has delete but NO edit (edit is text-only)', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          content_type: 'media_card',
          body: { title: 'Shared Video' },
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByTitle('chat.edit')).toBeNull();
    expect(screen.getByTitle('chat.delete')).toBeDefined();
  });

  // ── Delete interaction ─────────────────────────────────────────────────────

  it('clicking delete with window.confirm=true calls onDelete with the message id', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onDelete = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({
          id: 'msg-del-123',
          sender_id: 'U1',
          sender_type: 'user',
        })}
        currentUserId="U1"
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.delete'));
    expect(onDelete).toHaveBeenCalledWith('msg-del-123');
  });

  it('clicking delete with window.confirm=false does NOT call onDelete', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const onDelete = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U1', sender_type: 'user' })}
        currentUserId="U1"
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.delete'));
    expect(onDelete).not.toHaveBeenCalled();
  });

  // ── Edit interaction ───────────────────────────────────────────────────────

  it('clicking edit opens a textarea with the current text', () => {
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          body: { text: 'original text' },
        })}
        currentUserId="U1"
        onEdit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.edit'));
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(textarea).toBeDefined();
    expect(textarea.value).toBe('original text');
  });

  it('save with changed text calls onEdit with the new text', () => {
    const onEdit = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({
          id: 'msg-edit-456',
          sender_id: 'U1',
          sender_type: 'user',
          body: { text: 'original text' },
        })}
        currentUserId="U1"
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.edit'));
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'updated text' } });
    fireEvent.click(screen.getByText('chat.save'));
    expect(onEdit).toHaveBeenCalledWith('msg-edit-456', 'updated text');
  });

  it('save with unchanged text does NOT call onEdit', () => {
    const onEdit = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          body: { text: 'original text' },
        })}
        currentUserId="U1"
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.edit'));
    // Do not change the draft — it stays 'original text'
    fireEvent.click(screen.getByText('chat.save'));
    expect(onEdit).not.toHaveBeenCalled();
  });

  it('save with empty text does NOT call onEdit', () => {
    const onEdit = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          body: { text: 'original text' },
        })}
        currentUserId="U1"
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.edit'));
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: '' } });
    fireEvent.click(screen.getByText('chat.save'));
    expect(onEdit).not.toHaveBeenCalled();
  });

  it('cancel closes edit mode without calling onEdit', () => {
    const onEdit = vi.fn();
    render(
      <MessageBubble
        message={makeMessage({
          sender_id: 'U1',
          sender_type: 'user',
          body: { text: 'original text' },
        })}
        currentUserId="U1"
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByTitle('chat.edit'));
    expect(screen.getByRole('textbox')).toBeDefined();
    fireEvent.click(screen.getByText('chat.cancel'));
    expect(onEdit).not.toHaveBeenCalled();
    // After cancel, back to normal view (textarea gone)
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});

// ── Sender display-name resolution (#sender-name fix) ──────────────────────────

describe('MessageBubble sender name resolution', () => {
  it('resolves sender_id → member name via memberNameById (not the raw UUID)', () => {
    render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U2', body: { text: 'hi' } })}
        currentUserId="U1"
        memberNameById={{ U2: 'Alice' }}
      />,
    );
    expect(screen.getByText('Alice')).toBeDefined();
    expect(screen.queryByText('U2')).toBeNull();
  });

  it('falls back to body.sender_name when sender_id is not in the map', () => {
    render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U9', body: { text: 'hi', sender_name: 'Bob' } })}
        currentUserId="U1"
        memberNameById={{ U2: 'Alice' }}
      />,
    );
    expect(screen.getByText('Bob')).toBeDefined();
  });

  it('falls back to the raw id only when neither map nor body name is present', () => {
    render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U9', body: { text: 'hi' } })}
        currentUserId="U1"
        memberNameById={{ U2: 'Alice' }}
      />,
    );
    expect(screen.getAllByText('U9').length).toBeGreaterThan(0);
  });

  it('shows "Agent" for an agent message with no name hint', () => {
    render(
      <MessageBubble
        message={makeMessage({ sender_id: null, sender_type: 'agent', body: { text: 'hi' } })}
        currentUserId="U1"
        memberNameById={{ U2: 'Alice' }}
      />,
    );
    expect(screen.getByText('Agent')).toBeDefined();
  });
});

// ── Right/left alignment (own vs others, chat-app layout) ─────────────────────

describe('MessageBubble alignment', () => {
  it('own message: row reversed (right side) + accent bubble', () => {
    const { container } = render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U1', body: { text: 'mine' } })}
        currentUserId="U1"
      />,
    );
    expect(container.querySelector('.flex-row-reverse')).not.toBeNull();
    expect(container.querySelector('.items-end')).not.toBeNull();
    // accent (indigo) bubble for own text
    expect(container.innerHTML).toMatch(/indigo-500/);
  });

  it("other's message: left side (not reversed) + neutral card bubble", () => {
    const { container } = render(
      <MessageBubble
        message={makeMessage({ sender_id: 'U2', body: { text: 'theirs' } })}
        currentUserId="U1"
      />,
    );
    expect(container.querySelector('.flex-row-reverse')).toBeNull();
    expect(container.querySelector('.items-start')).not.toBeNull();
  });
});
