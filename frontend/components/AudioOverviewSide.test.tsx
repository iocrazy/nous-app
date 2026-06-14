import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { AudioOverviewSide } from './AudioOverviewSide';
import type { Video } from '../types';

// i18n + data-layer stubs so the real EagleTagPicker renders offline.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('../supabaseClient', () => ({ getSupabaseClient: () => null }));
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue(null),
}));
vi.mock('../services/resourceService', () => ({
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../services/tagPreferencesService', () => ({
  fetchTagPreferences: vi.fn().mockResolvedValue({
    starred_tag_ids: [],
    picker_settings: { layout: 'list', columnWidth: 'medium', showStarred: true, showRecently: true, showRecommended: false, showCount: true },
    panel_size: { width: 480, height: 400 },
  }),
  updateTagPreferences: vi.fn().mockResolvedValue(undefined),
}));

const baseVideo = {
  id: '123',
  media_type: 'audio',
  title: 'Here with me (Slow)',
  author: '人面兽心',
  comment_count: 1200,
  share_count: 4600,
  favorite_count: 338000,
} as unknown as Video;

describe('AudioOverviewSide', () => {
  it('renders the mock .side stack: title, artist, and formatted stats', () => {
    render(<AudioOverviewSide video={baseVideo} title="Here with me (Slow)" author="人面兽心" />);
    expect(screen.getByText('Here with me (Slow)')).toBeTruthy();
    expect(screen.getByText('@人面兽心')).toBeTruthy();
    // formatNumber: 1.2K / 4.6K / 338.0K
    expect(screen.getByText('1.2K')).toBeTruthy();
    expect(screen.getByText('4.6K')).toBeTruthy();
    expect(screen.getByText('338.0K')).toBeTruthy();
  });

  it('fires onRatingChange when a star is clicked', () => {
    const onRatingChange = vi.fn();
    render(
      <AudioOverviewSide video={baseVideo} title="t" rating={0} onRatingChange={onRatingChange} />,
    );
    // RatingStars renders 5 star buttons; clicking the 4th sets rating 4.
    const starButtons = screen.getAllByRole('button');
    fireEvent.click(starButtons[3]);
    expect(onRatingChange).toHaveBeenCalledWith(4);
  });

  it('shows the Add Tag affordance from the reused EagleTagPicker', () => {
    render(<AudioOverviewSide video={baseVideo} title="t" />);
    expect(screen.getByText('Add Tag')).toBeTruthy();
  });
});
