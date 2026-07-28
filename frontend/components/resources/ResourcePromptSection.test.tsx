import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import type { Tag } from '../../types';

// ─── Mocks ──────────────────────────────────────────────

const resourceRow = {
  id: 'r1',
  filename: 'a.png',
  file_type: 'image',
  gen_prompt: 'masterpiece',
  gen_prompt_zh: null,
  gen_prompt_negative: null,
  gen_prompt_negative_zh: null,
  gen_prompt_json: null,
};

const singleMock = vi.fn().mockResolvedValue({ data: resourceRow, error: null });
const eqMock = vi.fn(() => ({ single: singleMock }));
const selectMock = vi.fn(() => ({ eq: eqMock }));
const fromMock = vi.fn((..._args: unknown[]) => ({ select: selectMock }));

vi.mock('../../supabaseClient', () => ({
  supabase: { from: (...a: unknown[]) => fromMock(...a) },
}));

const tagNoTrigger: Tag = {
  id: 't1', name: 'Anime', color: '#fff', icon: null, type: 'user',
  prompt_trigger: false, created_at: '2026-01-01T00:00:00Z',
};
const triggerTag: Tag = {
  id: 't2', name: 'AI', color: '#6366f1', icon: null, type: 'user',
  prompt_trigger: true, created_at: '2026-01-01T00:00:00Z',
};

const fetchResourceTags = vi.fn().mockResolvedValue([{ tag: tagNoTrigger }]);
const addResourceTag = vi.fn().mockResolvedValue(undefined);
const updateResource = vi.fn().mockResolvedValue({});
const translateGenPrompt = vi.fn().mockResolvedValue({ gen_prompt_zh: '杰作' });

vi.mock('../../services/resourceService', () => ({
  fetchResourceTags: (...a: unknown[]) => fetchResourceTags(...a),
  addResourceTag: (...a: unknown[]) => addResourceTag(...a),
  updateResource: (...a: unknown[]) => updateResource(...a),
  translateGenPrompt: (...a: unknown[]) => translateGenPrompt(...a),
}));

const fetchAllTags = vi.fn().mockResolvedValue([tagNoTrigger, triggerTag]);
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
}));

const ensureDefaultTriggerTag = vi.fn().mockResolvedValue(triggerTag);
vi.mock('../../utils/promptTriggerTags', () => ({
  ensureDefaultTriggerTag: (...a: unknown[]) => ensureDefaultTriggerTag(...a),
}));

// Capture props passed to PromptSection instead of rendering the real thing —
// keeps this test focused on the wrapper's data wiring, not PromptSection's
// own rendering (already covered by PromptSection.test.tsx).
let capturedProps: any = null;
vi.mock('./PromptSection', () => ({
  PromptSection: (props: any) => {
    capturedProps = props;
    return <div data-testid="prompt-section" />;
  },
}));

import { ResourcePromptSection } from './ResourcePromptSection';

describe('ResourcePromptSection', () => {
  beforeEach(() => {
    capturedProps = null;
    singleMock.mockReset().mockResolvedValue({ data: resourceRow, error: null });
    fetchResourceTags.mockReset().mockResolvedValue([{ tag: tagNoTrigger }]);
    addResourceTag.mockReset().mockResolvedValue(undefined);
    updateResource.mockReset().mockResolvedValue({});
    translateGenPrompt.mockReset().mockResolvedValue({ gen_prompt_zh: '杰作' });
    fetchAllTags.mockReset().mockResolvedValue([tagNoTrigger, triggerTag]);
    ensureDefaultTriggerTag.mockReset().mockResolvedValue(triggerTag);
    fromMock.mockClear();
    selectMock.mockClear();
    eqMock.mockClear();
  });

  it('renders nothing while loading', () => {
    const { container } = render(<ResourcePromptSection resourceId="r1" />);
    expect(container.firstChild).toBeNull();
  });

  it('fetches the resource + tags in parallel and renders PromptSection with correct props', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    expect(fromMock).toHaveBeenCalledWith('resources');
    expect(selectMock).toHaveBeenCalledWith(
      expect.stringContaining('gen_prompt_negative_zh'),
    );
    expect(selectMock).toHaveBeenCalledWith(
      expect.stringContaining('gen_prompt_json'),
    );
    expect(eqMock).toHaveBeenCalledWith('id', 'r1');
    expect(fetchResourceTags).toHaveBeenCalledWith('r1');

    expect(capturedProps.resource.id).toBe('r1');
    expect(capturedProps.resource.gen_prompt).toBe('masterpiece');
    // Reverse-engineer is on for images (data-line PR, 2026-07-28) — the
    // wrapper self-manages Generate via PromptSection's own dispatch flow.
    expect(capturedProps.canGenerate).toBe(true);
    expect(typeof capturedProps.onGenerated).toBe('function');
  });

  it('canGenerate is false for non-image resources', async () => {
    singleMock.mockResolvedValueOnce({ data: { ...resourceRow, file_type: 'video' }, error: null });
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    expect(capturedProps.canGenerate).toBe(false);
  });

  it('onPatch PATCHes via updateResource and merges the field locally', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    await act(async () => {
      capturedProps.onPatch({ gen_prompt: 'new prompt' });
    });

    expect(updateResource).toHaveBeenCalledWith('r1', expect.objectContaining({ gen_prompt: 'new prompt' }));
    await waitFor(() => expect(capturedProps.resource.gen_prompt).toBe('new prompt'));
  });

  it('onTranslate calls translateGenPrompt and merges the result', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    expect(capturedProps.translating).toBe(false);
    await act(async () => {
      capturedProps.onTranslate('zh');
    });

    expect(translateGenPrompt).toHaveBeenCalledWith('r1', 'zh');
    await waitFor(() => expect(capturedProps.resource.gen_prompt_zh).toBe('杰作'));
    expect(capturedProps.translating).toBe(false);
  });

  it('onEnsureTriggerTag applies the default trigger tag via addResourceTag and refetches tags', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    fetchResourceTags.mockResolvedValueOnce([{ tag: tagNoTrigger }, { tag: triggerTag }]);

    await act(async () => {
      await capturedProps.onEnsureTriggerTag();
    });

    expect(fetchAllTags).toHaveBeenCalled();
    expect(ensureDefaultTriggerTag).toHaveBeenCalled();
    expect(addResourceTag).toHaveBeenCalledWith('r1', 't2');
    expect(fetchResourceTags).toHaveBeenCalledTimes(2); // initial load + refetch
  });

  it('does not re-add the trigger tag when the asset already carries it', async () => {
    fetchResourceTags.mockResolvedValueOnce([{ tag: triggerTag }]);
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    await act(async () => {
      await capturedProps.onEnsureTriggerTag();
    });

    expect(addResourceTag).not.toHaveBeenCalled();
  });

  it('calls onTagsChanged after the ensure flow actually writes a new tag', async () => {
    const onTagsChanged = vi.fn();
    fetchResourceTags.mockResolvedValueOnce([{ tag: tagNoTrigger }]); // initial load
    fetchResourceTags.mockResolvedValueOnce([{ tag: tagNoTrigger }, { tag: triggerTag }]); // refetch
    render(<ResourcePromptSection resourceId="r1" onTagsChanged={onTagsChanged} />);
    await screen.findByTestId('prompt-section');

    await act(async () => {
      await capturedProps.onEnsureTriggerTag();
    });

    expect(addResourceTag).toHaveBeenCalledWith('r1', 't2');
    expect(onTagsChanged).toHaveBeenCalledTimes(1);
  });

  it('onGenerated refetches the resource + tags and notifies onTagsChanged', async () => {
    const onTagsChanged = vi.fn();
    render(<ResourcePromptSection resourceId="r1" onTagsChanged={onTagsChanged} />);
    await screen.findByTestId('prompt-section');
    fromMock.mockClear();
    selectMock.mockClear();
    eqMock.mockClear();

    singleMock.mockResolvedValueOnce({
      data: { ...resourceRow, gen_prompt: 'masterpiece', gen_prompt_json: '{"subject":"cat"}' },
      error: null,
    });
    fetchResourceTags.mockResolvedValueOnce([{ tag: tagNoTrigger }, { tag: triggerTag }]);

    await act(async () => {
      await capturedProps.onGenerated();
    });

    expect(fromMock).toHaveBeenCalledWith('resources');
    expect(fetchResourceTags).toHaveBeenCalledWith('r1');
    expect(capturedProps.resource.gen_prompt_json).toBe('{"subject":"cat"}');
    expect(onTagsChanged).toHaveBeenCalledTimes(1);
  });

  it('onGenerated logs and bails when the refetch errors', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ResourcePromptSection resourceId="r1" />);
    await screen.findByTestId('prompt-section');

    singleMock.mockResolvedValueOnce({ data: null, error: new Error('boom') });

    await act(async () => {
      await capturedProps.onGenerated();
    });

    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('does not call onTagsChanged when the tag was already present', async () => {
    const onTagsChanged = vi.fn();
    fetchResourceTags.mockResolvedValueOnce([{ tag: triggerTag }]);
    render(<ResourcePromptSection resourceId="r1" onTagsChanged={onTagsChanged} />);
    await screen.findByTestId('prompt-section');

    await act(async () => {
      await capturedProps.onEnsureTriggerTag();
    });

    expect(addResourceTag).not.toHaveBeenCalled();
    expect(onTagsChanged).not.toHaveBeenCalled();
  });
});
