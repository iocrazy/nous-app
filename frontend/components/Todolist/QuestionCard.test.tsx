/**
 * Phase 2a Task 7 — QuestionCard: ONE typed-question card, mounted in the
 * issue NeedsInputCard, the cockpit waiting state, the Task Center feed and
 * the chat bubble. The label IS the value the backend validates against
 * (`answer_matches`), so a button must answer with the label verbatim plus
 * the question id the answer is for.
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { QuestionCard } from './QuestionCard';
import type { TypedQuestion } from './questionTypes';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown, arg3?: unknown) => {
      const vars = (typeof arg2 === 'object' && arg2) || (typeof arg3 === 'object' && arg3) || null;
      const fallback = typeof arg2 === 'string' ? arg2 : key;
      return vars ? `${fallback}:${Object.values(vars as Record<string, unknown>).join(',')}` : fallback;
    },
  }),
}));

afterEach(cleanup);

const q: TypedQuestion = {
  id: 'q:1:2',
  kind: 'user',
  prompt: 'Which ending?',
  options: [{ label: 'Open ending' }, { label: 'Twist', description: 'A last-minute reversal' }],
  allowFreeText: true,
};

describe('QuestionCard', () => {
  it('renders the prompt and one button per option, description as title', () => {
    render(<QuestionCard question={q} onAnswer={vi.fn()} />);
    expect(screen.getByText('Which ending?')).toBeTruthy();
    const buttons = screen.getAllByTestId('question-option');
    expect(buttons.map((b) => b.textContent)).toEqual(['Open ending', 'Twist']);
    expect(buttons[1].getAttribute('title')).toBe('A last-minute reversal');
  });

  it('option click answers with the label and the question id', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard question={q} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Twist' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('Twist', 'q:1:2'));
  });

  it('disables everything while an answer is in flight', async () => {
    let release: () => void = () => {};
    const onAnswer = vi.fn(() => new Promise<void>((r) => { release = r; }));
    render(<QuestionCard question={q} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Twist' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open ending' })).toBeDisabled());
    expect(screen.getByRole('textbox')).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open ending' })).not.toBeDisabled());
  });

  it('free text submits the typed answer with the question id', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard question={q} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Neither — a cliffhanger' } });
    fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('Neither — a cliffhanger', 'q:1:2'));
  });

  it('free text is disabled when allowFreeText is false', () => {
    render(<QuestionCard question={{ ...q, allowFreeText: false }} onAnswer={vi.fn()} />);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Answer' })).toBeNull();
  });

  it('answered state renders read-only with the pick highlighted', () => {
    render(<QuestionCard question={{ ...q, answered: { value: 'Twist' } }} onAnswer={vi.fn()} />);
    const btn = screen.getByRole('button', { name: 'Twist' });
    expect(btn).toBeDisabled();
    expect(btn.className).toMatch(/border-ok-line/);
    expect(screen.getByRole('button', { name: 'Open ending' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Open ending' }).className).not.toMatch(/border-ok-line/);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText('Answered')).toBeTruthy();
  });

  it('superseded state greys out and says it is no longer waiting', () => {
    render(
      <QuestionCard question={{ ...q, answered: { value: null, superseded: true } }} onAnswer={vi.fn()} />,
    );
    expect(screen.getByText('No longer waiting')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Twist' })).toBeDisabled();
    expect(screen.getByTestId('question-card').className).toMatch(/opacity/);
  });

  it('shows a typed error and re-enables when the answer is rejected', async () => {
    const onAnswer = vi.fn().mockRejectedValue(new Error('budget_still_exhausted'));
    render(<QuestionCard question={q} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Twist' }));
    await waitFor(() => expect(screen.getByTestId('question-error').textContent).toContain('budget_still_exhausted'));
    expect(screen.getByRole('button', { name: 'Twist' })).not.toBeDisabled();
  });

  it('uses only semantic colour tokens', () => {
    const { container } = render(<QuestionCard question={q} onAnswer={vi.fn()} />);
    const classes = Array.from(container.querySelectorAll('*')).map((e) => e.className).join(' ');
    expect(classes).not.toMatch(/\b(indigo|amber|red|emerald|blue|green|yellow)-\d/);
  });
});
