import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the underlying tag plumbing so we assert this module's orchestration
// (lookup / lazy-create / toggle) rather than HTTP.
const { fetchAllTags, createTag, addResourceTag, removeResourceTag } = vi.hoisted(() => ({
  fetchAllTags: vi.fn(),
  createTag: vi.fn(),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));

vi.mock('./unifiedTagService', () => ({
  fetchAllTags, createTag, addResourceTag, removeResourceTag,
}));

import {
  TO_PUBLISH_TAG_NAME,
  TO_PUBLISH_TAG_COLOR,
  findToPublishTagId,
  ensureToPublishTagId,
  toggleToPublish,
  hasToPublishTag,
} from './toPublishService';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('findToPublishTagId', () => {
  it('matches the well-known tag case-insensitively', async () => {
    fetchAllTags.mockResolvedValue([
      { id: 't1', name: 'Something' },
      { id: 't2', name: 'to publish' }, // different casing on purpose
    ]);
    expect(await findToPublishTagId()).toBe('t2');
  });

  it('returns null when no matching tag exists', async () => {
    fetchAllTags.mockResolvedValue([{ id: 't1', name: 'Draft' }]);
    expect(await findToPublishTagId()).toBeNull();
  });
});

describe('ensureToPublishTagId', () => {
  it('reuses an existing tag without creating a new one', async () => {
    fetchAllTags.mockResolvedValue([{ id: 't2', name: 'To Publish' }]);
    expect(await ensureToPublishTagId()).toBe('t2');
    expect(createTag).not.toHaveBeenCalled();
  });

  it('lazily creates the tag on first use', async () => {
    fetchAllTags.mockResolvedValue([]);
    createTag.mockResolvedValue({ id: 'new-1', name: TO_PUBLISH_TAG_NAME });
    expect(await ensureToPublishTagId()).toBe('new-1');
    expect(createTag).toHaveBeenCalledWith({
      name: TO_PUBLISH_TAG_NAME,
      color: TO_PUBLISH_TAG_COLOR,
    });
  });
});

describe('toggleToPublish', () => {
  it('adds the tag when not currently marked and reports the new state', async () => {
    fetchAllTags.mockResolvedValue([{ id: 't2', name: 'To Publish' }]);
    const result = await toggleToPublish('res-9', false);
    expect(addResourceTag).toHaveBeenCalledWith('res-9', 't2');
    expect(removeResourceTag).not.toHaveBeenCalled();
    expect(result).toBe(true);
  });

  it('removes the tag when currently marked and reports the new state', async () => {
    fetchAllTags.mockResolvedValue([{ id: 't2', name: 'To Publish' }]);
    const result = await toggleToPublish('res-9', true);
    expect(removeResourceTag).toHaveBeenCalledWith('res-9', 't2');
    expect(addResourceTag).not.toHaveBeenCalled();
    expect(result).toBe(false);
  });

  it('creates the tag first when it does not exist yet', async () => {
    fetchAllTags.mockResolvedValue([]);
    createTag.mockResolvedValue({ id: 'new-1', name: TO_PUBLISH_TAG_NAME });
    await toggleToPublish('res-9', false);
    expect(createTag).toHaveBeenCalledOnce();
    expect(addResourceTag).toHaveBeenCalledWith('res-9', 'new-1');
  });
});

describe('hasToPublishTag', () => {
  it('detects the mark case-insensitively', () => {
    expect(hasToPublishTag([{ name: 'draft' }, { name: 'TO PUBLISH' }])).toBe(true);
  });

  it('is false for lists without the mark, and for nullish input', () => {
    expect(hasToPublishTag([{ name: 'draft' }])).toBe(false);
    expect(hasToPublishTag([])).toBe(false);
    expect(hasToPublishTag(undefined)).toBe(false);
    expect(hasToPublishTag(null)).toBe(false);
  });
});
