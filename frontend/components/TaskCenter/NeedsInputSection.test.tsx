import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NeedsInputSection } from './NeedsInputSection';
import type { NeedsInputItem } from '../../services/issuesService';

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
    const onAnswer = vi.fn(() => new Promise<void>(() => {})); // never resolves — assert pending state
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

  it('renders nothing when items is empty', () => {
    const { container } = renderSection([]);
    expect(container.firstChild).toBeNull();
  });
});
