import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ResourceInfoPanel } from './ResourceInfoPanel';
import type { Resource } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

vi.mock('../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

vi.mock('./EagleTagPicker', () => ({
  EagleTagPicker: () => <div data-testid="eagle-tag-picker" />,
}));

vi.mock('./resources/ResourcePromptSection', () => ({
  ResourcePromptSection: () => <div data-testid="resource-prompt-section" />,
}));

const resource: Resource = {
  id: 'r1',
  creator_id: 'u1',
  source_type: 'upload',
  media_id: null,
  filename: 'a.png',
  file_type: 'image',
  mime_type: 'image/png',
  file_path: null,
  file_size_bytes: null,
  duration_seconds: null,
  resolution: null,
  thumbnail_path: null,
  cover_image_path: null,
  current_version: 1,
  notes: null,
  gen_prompt: null,
  url: null,
  rating: 0,
  is_trashed: false,
  trashed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
} as unknown as Resource;

const noop = () => {};

describe('ResourceInfoPanel', () => {
  it('renders the prompt section when not read-only', () => {
    render(
      <ResourceInfoPanel
        resource={resource}
        allTags={[]}
        assignedTags={[]}
        readOnly={false}
        onClose={noop}
        onAddTag={noop}
        onRemoveTag={noop}
        onUpdate={noop}
      />,
    );
    expect(screen.getByTestId('resource-prompt-section')).toBeTruthy();
  });

  it('hides the prompt section for read-only (recycle-bin) resources', () => {
    render(
      <ResourceInfoPanel
        resource={resource}
        allTags={[]}
        assignedTags={[]}
        readOnly
        onClose={noop}
        onAddTag={noop}
        onRemoveTag={noop}
        onUpdate={noop}
      />,
    );
    expect(screen.queryByTestId('resource-prompt-section')).toBeNull();
  });
});
