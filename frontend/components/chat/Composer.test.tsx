/**
 * Composer.test.tsx — RTL tests for @mention dropdown logic.
 *
 * The textarea is uncontrolled: handleSend/handlePick read el.value directly.
 * Drive input via direct `.value` assignment + selectionStart, then
 * fireEvent.input() to trigger onInput/handleInput. For submit, fireEvent.keyDown
 * with { key: 'Enter' }.
 *
 * Word-boundary test (the #895 review bug): after picking both Alice (U1) and
 * Alice Smith (U2), set textarea.value = '@Alice Smith ' directly (simulating
 * user deleting the standalone @Alice token), then submit and assert that only
 * U2 is in mentionUserIds — @Alice must NOT match inside @Alice Smith.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Composer } from './Composer';

// Mock react-i18next: t(key) returns the key so tests never depend on English strings
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string) => k,
  }),
}));

// ── Test data ─────────────────────────────────────────────────────────────────

const MEMBERS = [
  { user_id: 'U1', label: 'Alice' },
  { user_id: 'U2', label: 'Alice Smith' },
];
const AGENTS = [{ slug: 'bot', label: 'Bot' }];

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Set the textarea value and cursor position, then fire 'input' so that
 * React's onInput handler (handleInput) runs — triggering mention detection.
 */
function typeInto(textarea: HTMLTextAreaElement, value: string): void {
  textarea.value = value;
  textarea.selectionStart = value.length;
  textarea.selectionEnd = value.length;
  fireEvent.input(textarea);
}

/**
 * Find a MentionDropdown candidate by its label text and mousedown it
 * (the dropdown uses onMouseDown to avoid blurring the textarea).
 */
function pickCandidate(labelText: string): void {
  const labelEl = screen.getByText(labelText);
  const btn = labelEl.closest('button');
  if (!btn) throw new Error(`No button found for label "${labelText}"`);
  fireEvent.mouseDown(btn);
}

/** Simulate pressing Enter on the textarea (without Shift). */
function pressEnter(textarea: HTMLTextAreaElement): void {
  fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter', bubbles: true });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

// Typed aliases so vi.fn<T>() produces a Mock<T> that TypeScript accepts as the
// Composer prop types (Mock<T> extends T, so the JSX props are compatible).
type SendFn = (text: string, mentionUserIds: string[]) => void;
type TypingFn = () => void;

describe('Composer @mention dropdown logic', () => {
  let onSend: ReturnType<typeof vi.fn<SendFn>>;
  let onTyping: ReturnType<typeof vi.fn<TypingFn>>;

  beforeEach(() => {
    onSend = vi.fn<SendFn>();
    onTyping = vi.fn<TypingFn>();
  });

  // ── Case 1: dropdown opens ─────────────────────────────────────────────────

  it('typing @Al opens MentionDropdown with matching candidates', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    typeInto(textarea, '@Al');

    // Both user labels match 'al'
    expect(screen.getByText('Alice')).toBeTruthy();
    expect(screen.getByText('Alice Smith')).toBeTruthy();
    // Bot does not match 'al' — should not appear
    expect(screen.queryByText('Bot')).toBeNull();
  });

  // ── Case 2: pick user → insert @label, submit → mentionUserIds includes id ──

  it('picking a user inserts @label and submits with that user id', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    typeInto(textarea, '@Al');
    pickCandidate('Alice');

    // Textarea must contain the inserted @Alice token
    expect(textarea.value).toContain('@Alice');

    pressEnter(textarea);

    expect(onSend).toHaveBeenCalledOnce();
    const [, mentionUserIds] = onSend.mock.calls[0] as [string, string[]];
    expect(mentionUserIds).toContain('U1');
  });

  // ── Case 3: pick agent → insert @slug, mentionUserIds empty ────────────────

  it('picking the agent inserts @slug and does not add any id to mentionUserIds', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    typeInto(textarea, '@B');
    pickCandidate('Bot');

    // Agents are inserted by slug, not label
    expect(textarea.value).toContain('@bot');

    pressEnter(textarea);

    expect(onSend).toHaveBeenCalledOnce();
    const [, mentionUserIds] = onSend.mock.calls[0] as [string, string[]];
    expect(mentionUserIds).toHaveLength(0);
  });

  // ── Case 4: word-boundary (the #895 review bug) ────────────────────────────
  //
  // handleSend checks "is @<label> still present?" via a regex.
  // Before the fix, `/@Alice(?:\s|$)/` matched inside `@Alice Smith`
  // because the space between "Alice" and "Smith" satisfies \s.
  // The correct behaviour: @Alice must NOT match inside @Alice Smith.

  it('word-boundary: @Alice alone is not matched inside @Alice Smith', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    // Step 1 — pick Alice (U1) → mentionMapRef gets U1→'Alice'
    typeInto(textarea, '@Al');
    pickCandidate('Alice');
    // textarea.value is now '@Alice '

    // Step 2 — pick Alice Smith (U2) → mentionMapRef gets U2→'Alice Smith'
    // Simulate typing @Al again after the first mention
    typeInto(textarea, '@Alice @Al');
    pickCandidate('Alice Smith');
    // textarea.value is now '@Alice @Alice Smith '

    // Step 3 — simulate user deleting the standalone @Alice token.
    // Set value directly WITHOUT firing input (so mentionMapRef still holds both U1 & U2).
    textarea.value = '@Alice Smith ';

    // Step 4 — submit
    pressEnter(textarea);

    expect(onSend).toHaveBeenCalledOnce();
    const [, mentionUserIds] = onSend.mock.calls[0] as [string, string[]];
    // U2 (Alice Smith) IS present as a standalone mention
    expect(mentionUserIds).toContain('U2');
    // U1 (Alice) is NOT present — @Alice should not match inside @Alice Smith
    expect(mentionUserIds).not.toContain('U1');
  });

  // ── Case 5: empty / whitespace → no send ──────────────────────────────────

  it('does not call onSend on empty or whitespace-only input', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    // Empty textarea
    pressEnter(textarea);
    expect(onSend).not.toHaveBeenCalled();

    // Whitespace only
    textarea.value = '   ';
    pressEnter(textarea);
    expect(onSend).not.toHaveBeenCalled();
  });

  // ── Case 6: onTyping fires on non-empty input only ─────────────────────────

  it('calls onTyping on non-empty input change but NOT when the field is empty', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    // Empty → no typing signal
    typeInto(textarea, '');
    expect(onTyping).not.toHaveBeenCalled();

    // Non-empty → typing signal fires
    typeInto(textarea, 'hello');
    expect(onTyping).toHaveBeenCalledOnce();
  });

  // ── Case 7a: Enter with dropdown OPEN picks candidate, does NOT submit ─────

  it('Enter with dropdown OPEN picks the active candidate instead of submitting', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    typeInto(textarea, '@Al');
    // Dropdown is open; activeIndex=0 → Alice is the active candidate
    expect(screen.getByText('Alice')).toBeTruthy();

    // Enter should pick Alice (not submit)
    pressEnter(textarea);

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea.value).toContain('@Alice');
  });

  // ── Case 7b: Enter with dropdown CLOSED submits ────────────────────────────

  it('Enter with dropdown CLOSED submits the message', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    // Set value directly — no input event → mention detection does not run
    // → mentionQuery stays null → dropdown stays closed
    textarea.value = 'hello world';

    pressEnter(textarea);

    expect(onSend).toHaveBeenCalledOnce();
    const [text] = onSend.mock.calls[0] as [string, string[]];
    expect(text).toBe('hello world');
  });

  // ── Case 7c: Esc closes dropdown without submitting ────────────────────────

  it('Escape closes the dropdown without submitting', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;

    typeInto(textarea, '@Al');
    expect(screen.getByText('Alice')).toBeTruthy(); // dropdown is open

    fireEvent.keyDown(textarea, { key: 'Escape', code: 'Escape', bubbles: true });

    // Dropdown must be gone
    expect(screen.queryByText('Alice')).toBeNull();
    expect(onSend).not.toHaveBeenCalled();
  });

  // ── Image-attach button gating (flag-off UX) ────────────────────────────────
  // The upload entry (Paperclip, title chat.attachResource) must only render
  // when the caller wires onAttachFiles. On the flag-off path ChatPage passes
  // undefined, so the button must be absent — otherwise clicking it silently
  // no-ops (the prod bug this guards against).

  it('hides the image-attach button when onAttachFiles is not provided', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    expect(screen.queryByTitle('chat.attachResource')).toBeNull();
  });

  it('shows the image-attach button when onAttachFiles is provided', () => {
    render(
      <Composer
        onSend={onSend}
        onTyping={onTyping}
        onAttachFiles={vi.fn()}
        members={MEMBERS}
        agents={AGENTS}
      />,
    );
    expect(screen.queryByTitle('chat.attachResource')).toBeTruthy();
  });
});

describe('Composer staged attachments', () => {
  const onSend = vi.fn();

  beforeEach(() => {
    onSend.mockClear();
  });

  const ATTACHMENTS = [
    { id: 'a1', name: 'shot.png', previewUrl: 'blob:mock-1' },
    { id: 'a2', name: 'frame.jpg', previewUrl: 'blob:mock-2' },
  ];

  it('renders a thumbnail per staged attachment', () => {
    render(
      <Composer onSend={onSend} attachments={ATTACHMENTS} onRemoveAttachment={vi.fn()} />,
    );
    expect(screen.getByTitle('shot.png')).toBeTruthy();
    expect(screen.getByTitle('frame.jpg')).toBeTruthy();
  });

  it('remove button reports the attachment id', () => {
    const onRemove = vi.fn();
    render(
      <Composer onSend={onSend} attachments={ATTACHMENTS} onRemoveAttachment={onRemove} />,
    );
    const removeButtons = screen.getAllByLabelText('chat.image.removeAttachment');
    fireEvent.click(removeButtons[0]);
    expect(onRemove).toHaveBeenCalledWith('a1');
  });

  it('allows sending with empty text when attachments are staged', () => {
    render(<Composer onSend={onSend} attachments={ATTACHMENTS} />);
    fireEvent.click(screen.getByTitle('chat.send'));
    expect(onSend).toHaveBeenCalledWith('', []);
  });

  it('still blocks empty sends when nothing is staged', () => {
    render(<Composer onSend={onSend} attachments={[]} />);
    fireEvent.click(screen.getByTitle('chat.send'));
    expect(onSend).not.toHaveBeenCalled();
  });
});
