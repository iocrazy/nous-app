import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NeedsInputSection } from './NeedsInputSection';
import type { NeedsInputItem } from '../../services/issuesService';
import { AgentNotDispatchedError } from '../../services/issueMessageService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// getIssue is only invoked from the "View conversation" click handler, which
// none of the tests below exercise — mocked so the module import doesn't
// need a real API base / fetch.
vi.mock('../../services/issuesService', async () => {
  const actual = await vi.importActual<typeof import('../../services/issuesService')>(
    '../../services/issuesService',
  );
  return { ...actual, getIssue: vi.fn() };
});

afterEach(cleanup);

const item = (over: Partial<NeedsInputItem> = {}): NeedsInputItem => ({
  issue_id: '1001',
  title: 'Confirm publish schedule',
  question: 'Should this go out on Monday or Wednesday?',
  project_id: null,
  team_id: '2002',
  asked_at: '2026-08-01T00:00:00Z',
  assignee_agent_id: 'a1',
  identifier: 'MH-1001',
  ...over,
});

function renderSection(items: NeedsInputItem[], onAnswer = vi.fn()) {
  return render(
    <MemoryRouter>
      <NeedsInputSection items={items} onAnswer={onAnswer} />
    </MemoryRouter>,
  );
}

describe('NeedsInputSection', () => {
  it('renders the section title, count badge, and the original question when items are present', () => {
    renderSection([
      item(),
      item({ issue_id: '1002', title: 'Second one', question: 'Which format?' }),
    ]);
    expect(screen.getByText('taskCenter.needsAnswer')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
    expect(
      screen.getByText('Should this go out on Monday or Wednesday?'),
    ).toBeTruthy();
  });

  it('calls onAnswer(issueId, text) on submit, clears the input, and puts the card in a pending state', async () => {
    // Never resolves — this test only covers the moment of submit (entering
    // pending). The full resolve → refetch lifecycle is covered separately
    // below, since a resolved promise must NOT by itself clear pending.
    const onAnswer = vi.fn(() => new Promise<void>(() => {}));
    renderSection([item()], onAnswer);

    const textarea = screen.getByPlaceholderText(
      'taskCenter.answerPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Wednesday works better.' } });
    expect(textarea.value).toBe('Wednesday works better.');

    const button = screen.getByRole('button', { name: /taskCenter\.answerButton/i });
    fireEvent.click(button);

    expect(onAnswer).toHaveBeenCalledWith('1001', 'Wednesday works better.');
    expect(textarea.value).toBe('');
    expect(textarea).toBeDisabled();
    expect(button).toBeDisabled();
  });

  it('keeps the card pending after onAnswer resolves — only a refetch that drops the row clears it, and a later reappearance is a fresh ask', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderSection([item()], onAnswer);

    const textarea = screen.getByPlaceholderText(
      'taskCenter.answerPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Wednesday works better.' } });
    fireEvent.click(screen.getByRole('button', { name: /taskCenter\.answerButton/i }));

    await waitFor(() =>
      expect(onAnswer).toHaveBeenCalledWith('1001', 'Wednesday works better.'),
    );

    // (a) resolve alone doesn't re-enable the card — `items` is still the
    // same snapshot (the parent hasn't refetched yet, or the backend's
    // needs_followup flip hasn't landed), so the row must stay pending
    // rather than reopening with an empty draft.
    await waitFor(() => {
      expect(screen.getByPlaceholderText('taskCenter.answerPlaceholder')).toBeDisabled();
    });

    // (b) refetch-without-the-row removes it — the parent's next snapshot
    // no longer contains this issue (the flip landed for real).
    rerender(
      <MemoryRouter>
        <NeedsInputSection items={[]} onAnswer={onAnswer} />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Confirm publish schedule')).toBeNull();

    // (c) refetch-with-the-row-again re-enables input — the issue reappears
    // (agent asked again, or the earlier flip didn't stick); this must read
    // as a fresh ask, not "still pending from before".
    rerender(
      <MemoryRouter>
        <NeedsInputSection items={[item()]} onAnswer={onAnswer} />
      </MemoryRouter>,
    );
    const reopened = screen.getByPlaceholderText(
      'taskCenter.answerPlaceholder',
    ) as HTMLTextAreaElement;
    expect(reopened).not.toBeDisabled();
    expect(reopened.value).toBe('');
  });

  it('renders nothing when items is empty', () => {
    const { container } = renderSection([]);
    expect(container.firstChild).toBeNull();
  });

  it('clears pending and shows an inline warning (no draft restore) when onAnswer rejects with AgentNotDispatchedError', async () => {
    // Finding 5 (final review): the message posted successfully server-side
    // (legacy/no-op path) but no agent turn started — TaskCenter's handler
    // signals this by rejecting with AgentNotDispatchedError rather than
    // resolving, so the row must not sit "pending" forever with no
    // feedback, and must not treat it like a network failure (which would
    // restore the draft for retry and re-post a duplicate).
    const onAnswer = vi.fn().mockRejectedValue(new AgentNotDispatchedError());
    renderSection([item()], onAnswer);

    const textarea = screen.getByPlaceholderText(
      'taskCenter.answerPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Wednesday works better.' } });
    fireEvent.click(screen.getByRole('button', { name: /taskCenter\.answerButton/i }));

    await waitFor(() => expect(onAnswer).toHaveBeenCalled());

    await waitFor(() => {
      expect(screen.getByText('taskCenter.answerNotDispatched')).toBeTruthy();
    });
    // Card is interactive again (not stuck pending)...
    expect(screen.getByPlaceholderText('taskCenter.answerPlaceholder')).not.toBeDisabled();
    // ...but the draft is NOT restored — the message was already saved, so
    // resubmitting the same text would post a duplicate.
    expect(
      (screen.getByPlaceholderText('taskCenter.answerPlaceholder') as HTMLTextAreaElement)
        .value,
    ).toBe('');
  });

  it('restores the draft (no inline warning) when onAnswer rejects with a plain error', async () => {
    // Regression line: a genuine network/backend failure keeps its original
    // behavior — draft restored for retry, no dispatch-specific warning.
    const onAnswer = vi.fn().mockRejectedValue(new Error('network blip'));
    renderSection([item()], onAnswer);

    const textarea = screen.getByPlaceholderText(
      'taskCenter.answerPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Wednesday works better.' } });
    fireEvent.click(screen.getByRole('button', { name: /taskCenter\.answerButton/i }));

    await waitFor(() => expect(onAnswer).toHaveBeenCalled());

    await waitFor(() => {
      expect(
        (screen.getByPlaceholderText('taskCenter.answerPlaceholder') as HTMLTextAreaElement)
          .value,
      ).toBe('Wednesday works better.');
    });
    expect(screen.queryByText('taskCenter.answerNotDispatched')).toBeNull();
  });
});


describe('NeedsInputSection — typed question rows (phase 2a)', () => {
  const typedItem = () =>
    item({
      question_id: 'q:1:2',
      kind: 'user',
      options: [{ label: 'Monday' }, { label: 'Wednesday', description: 'Mid-week' }],
      allow_free_text: false,
    });

  it('shows the Pick one of N chip and answers with the label + question id', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    renderSection([typedItem()], onAnswer);
    expect(screen.getByTestId('needs-input-pick-one').textContent).toBe('question.pickOne');
    expect(screen.queryByPlaceholderText('taskCenter.answerPlaceholder')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Wednesday' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('1001', 'Wednesday', 'q:1:2'));
    // pending until the row leaves items
    expect(screen.getByRole('button', { name: 'Monday' })).toBeDisabled();
  });

  it('a plain row keeps the textarea and no chip', () => {
    renderSection([item()]);
    expect(screen.queryByTestId('needs-input-pick-one')).toBeNull();
    expect(screen.getByPlaceholderText('taskCenter.answerPlaceholder')).toBeTruthy();
  });

  it('not-dispatched on a typed answer shows the row warning, not the card error', async () => {
    const onAnswer = vi.fn().mockRejectedValue(new AgentNotDispatchedError());
    renderSection([typedItem()], onAnswer);
    fireEvent.click(screen.getByRole('button', { name: 'Monday' }));
    await waitFor(() => expect(screen.getByText('taskCenter.answerNotDispatched')).toBeTruthy());
    expect(screen.queryByTestId('question-error')).toBeNull();
    expect(screen.getByRole('button', { name: 'Monday' })).not.toBeDisabled();
  });
});
