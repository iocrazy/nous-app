/**
 * chat-stream 的 `error` SSE event 现在带 `code`(spec §2)。providerErrorMessage
 * 是纯函数：已知的 5 个 provider 错误码映射到 i18n 文案，未知码/非字符串一律
 * 返回 null 让调用方走原有兜底（旧 shape 的 `evt.data.error` 或固定文案）。
 *
 * REST 侧的 `ErrorResponse.code` 走同一份映射（见 ResourcePromptSection 的翻译
 * 失败回显），所以这个纯函数住在 utils/ 而不是某个组件里。
 */

import { describe, expect, it } from 'vitest';

import { providerErrorMessage } from './providerErrorMessage';

// 最小 t() stub：直接回显 key,能验证映射选中了哪个 key,不需要真的加载 i18n 资源。
const t = (key: string, fallback: string) => `${key}|${fallback}`;

describe('providerErrorMessage', () => {
  it.each([
    ['provider_rate_limit', 'errors.provider.providerRateLimit'],
    ['provider_unreachable', 'errors.provider.providerUnreachable'],
    ['provider_auth', 'errors.provider.providerAuth'],
    ['provider_bad_model', 'errors.provider.providerBadModel'],
    ['task_timeout', 'errors.provider.taskTimeout'],
    // codex-local 的四个码(backend/app/services/ai/error_catalog.py)。走的是同一条
    // SSE error 通道,所以共用这份映射——本机链路的失败不该退化成通用兜底文案。
    ['local_daemon_offline', 'errors.provider.localDaemonOffline'],
    ['local_tools_unsupported', 'errors.provider.localToolsUnsupported'],
    ['local_codex_not_logged_in', 'errors.provider.localCodexNotLoggedIn'],
    ['local_codex_failed', 'errors.provider.localCodexFailed'],
  ])('maps known code %s to i18n key %s', (code, expectedKey) => {
    expect(providerErrorMessage(code, t)).toBe(`${expectedKey}|${code}`);
  });

  it('returns null for an unknown code', () => {
    expect(providerErrorMessage('some_other_code', t)).toBeNull();
  });

  it.each([undefined, null, 123, {}, []])(
    'returns null for a non-string code (%j)',
    (code) => {
      expect(providerErrorMessage(code, t)).toBeNull();
    },
  );
});
