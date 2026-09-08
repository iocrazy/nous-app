/**
 * A2 —— 详情页提问卡：agent 停下来问问题时，在原地回复、原地续跑。
 *
 * 失败分型照抄 TaskCenter NeedsInputSection 的契约（CLAUDE.md「触发路径必须
 * 类型化失败回显」）：AgentNotDispatchedError 说明消息已存但没起 turn ——
 * 显示专属提示且**不恢复草稿**（重发会灌重复消息）；普通网络错误才恢复草稿。
 */

import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NeedsInputCard } from './NeedsInputCard';
import { AgentNotDispatchedError } from '../../services/issueMessageService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

afterEach(cleanup);

const QUESTION = 'Should the pilot open cold or with a teaser?';

function renderCard(onSubmit = vi.fn(), question: string | null = QUESTION) {
  return render(
    <NeedsInputCard question={question} agentName="Script Writer" onSubmit={onSubmit} />,
  );
}

function textarea() {
  return screen.getByPlaceholderText('Reply here…') as HTMLTextAreaElement;
}

describe('NeedsInputCard', () => {
  it('renders the card with the agent question verbatim', () => {
    const { container } = renderCard();
    const card = container.querySelector('[data-testid="needs-input-card"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.textContent).toMatch(/Agent needs your answer/);
    expect(card.textContent).toContain(QUESTION);
  });

  it('submits the draft and clears the box', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderCard(onSubmit);
    fireEvent.change(textarea(), { target: { value: 'Open cold.' } });
    fireEvent.click(screen.getByRole('button', { name: /reply/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('Open cold.'));
    expect(textarea().value).toBe('');
  });

  it('shows the not-dispatched warning and does NOT restore the draft', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new AgentNotDispatchedError());
    const { container } = renderCard(onSubmit);
    fireEvent.change(textarea(), { target: { value: 'Open cold.' } });
    fireEvent.click(screen.getByRole('button', { name: /reply/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await waitFor(() => {
      expect(container.querySelector('[data-testid="needs-input-not-dispatched"]')).not.toBeNull();
    });
    expect(textarea()).not.toBeDisabled();
    expect(textarea().value).toBe('');
  });

  it('restores the draft on a plain error so the reply can be retried', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('network blip'));
    const { container } = renderCard(onSubmit);
    fireEvent.change(textarea(), { target: { value: 'Open cold.' } });
    fireEvent.click(screen.getByRole('button', { name: /reply/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await waitFor(() => expect(textarea().value).toBe('Open cold.'));
    expect(container.querySelector('[data-testid="needs-input-not-dispatched"]')).toBeNull();
  });

  it('still renders when the agent asked without a stated reason', () => {
    const { container } = renderCard(vi.fn(), null);
    expect(container.querySelector('[data-testid="needs-input-card"]')).not.toBeNull();
  });
});


describe('NeedsInputCard — typed question (phase 2a)', () => {
  const typed = {
    id: 'q:1:2',
    kind: 'user',
    prompt: QUESTION,
    options: [{ label: 'Cold open' }, { label: 'Teaser' }],
    allowFreeText: false,
  };

  it('mounts QuestionCard instead of the textarea when the question has options', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <NeedsInputCard
        question={QUESTION}
        onSubmit={vi.fn()}
        typed={{ ...typed, prompt: QUESTION.slice(0, 20) }}
        onAnswer={onAnswer}
      />,
    );
    expect(container.querySelector('textarea')).toBeNull();
    expect(container.querySelector('[data-testid="question-card"]')).not.toBeNull();
    // the prompt is shown once (by the card header), not twice — even when
    // the marker's clipped prompt differs from the outcome_reason text
    expect(container.textContent?.split(QUESTION).length).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'Teaser' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('Teaser', 'q:1:2'));
  });

  it('an OPEN-ENDED typed question still answers through the card (answer_to travels)', async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <NeedsInputCard
        question={QUESTION}
        onSubmit={vi.fn()}
        typed={{ ...typed, options: [], allowFreeText: true }}
        onAnswer={onAnswer}
      />,
    );
    expect(container.querySelector('textarea')).toBeNull();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Cold open, then a title card' } });
    fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('Cold open, then a title card', 'q:1:2'));
  });

  it('keeps the textarea for a plain needs_input park (no typed question)', () => {
    const { container } = render(
      <NeedsInputCard question={QUESTION} onSubmit={vi.fn()} typed={null} />,
    );
    expect(container.querySelector('textarea')).not.toBeNull();
    expect(container.querySelector('[data-testid="question-card"]')).toBeNull();
  });

  it('shows the not-dispatched notice when the typed answer was saved but nothing started', async () => {
    const onAnswer = vi.fn().mockRejectedValue(new AgentNotDispatchedError());
    const { container } = render(
      <NeedsInputCard question={QUESTION} onSubmit={vi.fn()} typed={typed} onAnswer={onAnswer} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Teaser' }));
    await waitFor(() => {
      expect(container.querySelector('[data-testid="needs-input-not-dispatched"]')).not.toBeNull();
    });
    expect(container.querySelector('[data-testid="question-error"]')).toBeNull();
  });
});
