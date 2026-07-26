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
});
