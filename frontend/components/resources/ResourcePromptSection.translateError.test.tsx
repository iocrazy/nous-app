/**
 * 翻译失败必须有**用户可见**的类型化回显（仓库纪律「触发路径必须类型化失败
 * 回显，silent no-op 不可接受」）。
 *
 * 在 batch-fallback-rollout 之前，`handleTranslate` 只 `console.error`：翻译一段
 * 成功一段 429 会返回 200 并写入半个结果，用户至少看得到变化；改成整体 503 且
 * 不落库之后，同一个按钮变成「点了毫无反应」。所以这两条断言的是 DOM 里真的
 * 出现了文案，不是 console 被调用过。
 *
 * `t` 桩回显 key，这样断言钉的是「选中了哪条 i18n 文案」而不是英文默认串。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => `${k}|${d ?? ''}` }),
}));

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
vi.mock('../../supabaseClient', () => ({
  supabase: {
    from: () => ({ select: () => ({ eq: () => ({ single: singleMock }) }) }),
  },
}));

const translateGenPrompt = vi.fn();
vi.mock('../../services/resourceService', () => ({
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  updateResource: vi.fn().mockResolvedValue({}),
  generateSlidePrompt: vi.fn().mockResolvedValue({}),
  translateGenPrompt: (...a: unknown[]) => translateGenPrompt(...a),
}));

vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../utils/promptTriggerTags', () => ({
  ensureDefaultTriggerTag: vi.fn(),
  hasPromptData: () => true,
}));

let capturedProps: any = null;
vi.mock('./PromptSection', () => ({
  PromptSection: (props: any) => {
    capturedProps = props;
    return <div data-testid="prompt-section" />;
  },
}));

import { ToastProvider } from '../Toast';
import { ResourcePromptSection } from './ResourcePromptSection';

async function mountAndFailTranslateWith(err: unknown) {
  render(
    <ToastProvider>
      <ResourcePromptSection resourceId="r1" />
    </ToastProvider>,
  );
  await screen.findByTestId('prompt-section');
  translateGenPrompt.mockRejectedValueOnce(err);
  await act(async () => {
    capturedProps.onTranslate('zh');
  });
}

describe('ResourcePromptSection — translate failure is visible', () => {
  beforeEach(() => {
    capturedProps = null;
    translateGenPrompt.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows the provider copy for a typed provider error', async () => {
    // 形状即 resourceService 从 ErrorResponse envelope 抛出的那个 Error。
    await mountAndFailTranslateWith(
      Object.assign(
        new Error('The model provider is rate-limiting requests. Try again shortly.'),
        { code: 'provider_rate_limit' },
      ),
    );

    expect(
      await screen.findByText(/errors\.provider\.providerRateLimit/),
    ).toBeInTheDocument();
  });

  it('falls back to the generic translation-failed copy for an unknown error', async () => {
    await mountAndFailTranslateWith(new Error('boom'));

    expect(
      await screen.findByText(/resources\.infoPanel\.promptTranslateFailed/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/errors\.provider\./)).toBeNull();
  });

  it('clears the translating flag either way', async () => {
    await mountAndFailTranslateWith(new Error('boom'));
    expect(capturedProps.translating).toBe(false);
  });
});
