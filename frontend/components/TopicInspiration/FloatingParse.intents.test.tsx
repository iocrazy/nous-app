// frontend/components/TopicInspiration/FloatingParse.intents.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f?: string) => f ?? _k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
const parseShareLink = vi.fn().mockResolvedValue({ title: 'ok' });
vi.mock('../../services/parserService', () => ({
  parseShareLink: (...a: unknown[]) => parseShareLink(...a),
  parseBatchLinks: vi.fn(),
  getSodaPlaylist: vi.fn(),
  downloadSodaTracks: vi.fn(),
}));
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([
    { id: '1', name: 'Transcript', type: 'system', group_name: 'Pipeline' },
    { id: '2', name: 'Cats', type: 'user', group_name: 'Animals' },
  ]),
  createTag: vi.fn(),
}));
vi.mock('../EagleTagPicker', () => ({
  EagleTagPicker: ({ allTags }: { allTags: Array<{ name: string }> }) => (
    <div data-testid="picker">{allTags.map((t) => t.name).join(',')}</div>
  ),
}));

import { FloatingParse } from './FloatingParse';

describe('FloatingParse AI intents', () => {
  it('sends transcribe/analyze booleans instead of tag ids and hides Pipeline tags', async () => {
    render(<FloatingParse open onOpenChange={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/paste/i), {
      target: { value: 'https://v.douyin.com/abc/' },
    });
    fireEvent.click(screen.getByTestId('ai-intent-transcribe'));
    fireEvent.click(screen.getByTestId('ai-intent-analyze'));
    await waitFor(() => expect(screen.getByTestId('picker').textContent).toBe('Cats'));
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    await waitFor(() => expect(parseShareLink).toHaveBeenCalled());
    const [, opts] = parseShareLink.mock.calls[0];
    expect(opts).toMatchObject({ transcribe: true, analyze: true });
    expect(opts.summarize).toBeFalsy();
    expect(opts.tag_ids).toEqual([]);
  });

  it('clears the AI intents when the panel is reset via Done', async () => {
    const { rerender } = render(<FloatingParse open onOpenChange={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/paste/i), {
      target: { value: 'https://v.douyin.com/abc/' },
    });
    fireEvent.click(screen.getByTestId('ai-intent-summarize'));
    expect(screen.getByTestId('ai-intent-summarize').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
    rerender(<FloatingParse open={false} onOpenChange={vi.fn()} />);
    rerender(<FloatingParse open onOpenChange={vi.fn()} />);
    expect(screen.getByTestId('ai-intent-summarize').getAttribute('aria-pressed')).toBe('false');
  });
});
