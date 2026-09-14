import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { ResourceInfoPanel } from './ResourceInfoPanel';
import { getResourceProvenance } from '../services/resourceService';
import type { Resource } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

vi.mock('../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
  getResourceProvenance: vi.fn(),
}));

// 来源块自己有一整套测试（OutputProvenance.test.tsx）。这里问的只有一件事：
// 面板有没有把**对的坐标**交给它 —— ref 是 generated_media 的 id，不是资源 id。
vi.mock('./agentActivity/OutputProvenance', () => ({
  OutputProvenance: (p: { refId: string; allowDiff?: boolean }) => (
    <div data-testid="provenance" data-ref={p.refId} data-diff={String(p.allowDiff)} />
  ),
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

const props = {
  resource,
  allTags: [],
  assignedTags: [],
  readOnly: false,
  onClose: noop,
  onAddTag: noop,
  onRemoveTag: noop,
  onUpdate: noop,
};

/** 真实 wire 形状：id 全是 string，`as_of_seq` 也是（Snowflake 掉精度）。 */
const chain = {
  kind: 'generated_media',
  ref_id: '347786145852739',
  latest_version: 1,
  as_of_seq: '347786145852739011',
  versions: [
    {
      id: '347786145852739011',
      version: 1,
      parent_version: null,
      run_id: '727145299382534100',
      actor_user_id: null,
      reverted_from_version: null,
      cost_kind: 'exact' as const,
      issue_id: '727145299382534000',
      issue_key: 'MH-94',
      deep_link: '/team/424242424242/todolist/MH-94?step=3',
      seq: 31,
      turn: 1,
      step: 3,
      title: 'Cover',
      model: 'gpt-6-astra',
      cost_cents: 12,
      created_at: '2026-09-12T02:00:00Z',
    },
  ],
};

beforeEach(() => {
  vi.mocked(getResourceProvenance).mockReset();
  vi.mocked(getResourceProvenance).mockResolvedValue(null);
});
afterEach(cleanup);

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

/**
 * harness 三期 3b Task 7b —— 这个资源是谁做的（spec §5 稿四）。
 *
 * 面板手上只有 `resource.id`；链接在 `generated_media.promoted_resource_id`
 * 一侧，`resources` 没有反向列，所以反查在后端，面板只负责把答案交给画布上
 * 用的**同一个**来源块。
 */
describe('ResourceInfoPanel — provenance', () => {
  it('shows where an agent-made resource came from', async () => {
    vi.mocked(getResourceProvenance).mockResolvedValue(chain);
    render(<ResourceInfoPanel {...props} resource={{ ...resource, id: '347786145852739000', source_type: 'generated' } as unknown as Resource} />);
    const block = await screen.findByTestId('provenance');
    // ref 是 generated_media 的 id（链上的 ref_id），不是资源 id。
    expect([block.getAttribute('data-ref'), block.getAttribute('data-diff')]).toEqual(['347786145852739', 'false']);
    expect(getResourceProvenance).toHaveBeenCalledWith('347786145852739000');
  });

  it('a human upload shows nothing', async () => {
    vi.mocked(getResourceProvenance).mockResolvedValue(null);
    render(<ResourceInfoPanel {...props} />);
    await waitFor(() => expect(getResourceProvenance).toHaveBeenCalled());
    expect(screen.queryByTestId('provenance')).toBeNull();
  });

  it('a failed read never breaks the panel', async () => {
    // 读不到来源不该让整个信息面板残缺 —— 但也不能静默：日志要说出来。
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(getResourceProvenance).mockRejectedValue(new Error('boom'));
    render(<ResourceInfoPanel {...props} />);
    await waitFor(() => expect(getResourceProvenance).toHaveBeenCalled());
    expect(screen.getByText('a.png')).toBeTruthy();
    expect(screen.queryByTestId('provenance')).toBeNull();
    await waitFor(() => expect(logged).toHaveBeenCalled());
    logged.mockRestore();
  });
});
