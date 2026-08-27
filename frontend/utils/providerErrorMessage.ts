/**
 * 后端 provider 错误码 → i18n 文案。
 *
 * 码由 `backend/app/core/provider_errors.py` 产出，走两条路到前端：chat-stream
 * 的 `error` SSE event（`evt.data.code`），以及普通 REST 的 `ErrorResponse.code`。
 * 两条路共用这一份映射，所以「哪些码有用户文案」只有一个答案。
 *
 * 未知码/非字符串一律返回 null，让调用方走自己的兜底文案——我们不替后端发明码。
 *
 * 与 `utils/errorCatalog.ts` 的分工：那边吃的是 `task_tracking.metadata` 里的
 * 大写 `error_code`（DBOS 任务失败的事后回看），这边吃的是同步请求当场返回的
 * 小写码。
 */

/** 已知 provider 错误码 → i18n 文案;未知码返回 null 走原有兜底。 */
export function providerErrorMessage(
  code: unknown,
  t: (key: string, fallback: string) => string,
): string | null {
  const KNOWN = [
    'provider_rate_limit',
    'provider_unreachable',
    'provider_auth',
    'provider_bad_model',
    'task_timeout',
    // codex-local 链路(backend/app/services/ai/error_catalog.py)。这四个码的共同点
    // 是修复动作在**用户自己的电脑上**——通用的 "provider unreachable" 会把人指向
    // 平台状态页,而真正要做的是启动 daemon / 跑 codex login / 解绑 Skill。
    'local_daemon_offline',
    'local_tools_unsupported',
    'local_codex_not_logged_in',
    'local_codex_failed',
    'local_ref_rejected',
    'local_cli_missing',
  ];
  if (typeof code !== 'string' || !KNOWN.includes(code)) return null;
  const key = code
    .split('_')
    .map((w, i) => (i === 0 ? w : w[0].toUpperCase() + w.slice(1)))
    .join('');
  return t(`errors.provider.${key}`, code);
}
