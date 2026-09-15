import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const fetchAllTags = vi.fn();
const updateTag = vi.fn();
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  updateTag: (...a: unknown[]) => updateTag(...a),
  createTag: vi.fn(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { PromptTriggerTagsCard } from './PromptTriggerTagsCard';

const tags = [
  { id: '1', name: 'AI', prompt_trigger: true, type: 'user', color: '#6366f1' },
  { id: '2', name: 'cyberpunk', prompt_trigger: false, type: 'user', color: '#818cf8' },
];

beforeEach(() => {
  fetchAllTags.mockResolvedValue(tags);
  updateTag.mockResolvedValue({ ...tags[1], prompt_trigger: true });
});

describe('PromptTriggerTagsCard', () => {
  it('lists trigger tags as chips', async () => {
    render(<PromptTriggerTagsCard />);
    expect(await screen.findByText('AI')).toBeTruthy();
    expect(screen.queryByText('cyberpunk')).toBeNull();
  });

  it('removing a chip clears prompt_trigger', async () => {
    render(<PromptTriggerTagsCard />);
    fireEvent.click(await screen.findByLabelText('Remove AI'));
    await waitFor(() => expect(updateTag).toHaveBeenCalledWith('1', { prompt_trigger: false }));
  });

  // This used to assert that `type: 'system'` rows were filtered OUT of the
  // candidate list. Mig 468 deleted those rows — the initial tags are ordinary
  // tags of the user's now — so the filter that did it would only ever hide
  // tags they own. What is left to check is the real rule: already-triggering
  // tags are not offered again.
  it('offers your tags that are not already prompt triggers', async () => {
    fetchAllTags.mockResolvedValue([
      { id: '1', name: 'AI', prompt_trigger: true, type: 'user', color: '#6366f1' },
      { id: '2', name: 'cyberpunk', prompt_trigger: false, type: 'user', color: '#818cf8' },
      { id: '3', name: 'Tutorial', prompt_trigger: false, type: 'user', color: '#3b82f6' },
    ]);

    render(<PromptTriggerTagsCard />);
    fireEvent.click(screen.getByText(/Add tag/));

    await waitFor(() => {
      const select = screen.getByRole('combobox') as HTMLSelectElement;
      const optionTexts = Array.from(select.options).map((o) => o.textContent);
      expect(optionTexts).toContain('cyberpunk');
      // An initial tag is a candidate like any other.
      expect(optionTexts).toContain('Tutorial');
      // Already a trigger — not offered again.
      expect(optionTexts).not.toContain('AI');
    });
  });
});
