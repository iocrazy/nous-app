// Turn a failed task's structured error code into actionable, translated copy.
//
// WHY THIS EXISTS
// `task_tracking.error_msg` is written one-way by the DBOS engine (route C
// discipline, see CLAUDE.md), so the backend can't rewrite it into something
// a user can act on. A provider 401 during image captioning reached the UI as:
//
//   DBOSMaxStepRetriesExceeded: Step ai_caption_via_provider has exceeded
//   its maximum of 3 retries
//
// — which reads like a platform bug when the real fix was one field in
// Settings → AI. The backend now classifies the underlying exception into a
// stable code and merges it into the business-owned `metadata` jsonb
// (`backend/app/services/ai/error_catalog.py`); this module maps that code to
// i18n copy.
//
// RELATIONSHIP TO humanizeTaskError
// humanizeTaskError guesses from the raw English string and stays the right
// tool where no code exists (downloads, publishing, older rows). This module
// is the structured path: when the backend told us WHAT went wrong, we render
// translated, actionable copy instead of pattern-matching prose. No code
// present → we don't invent one; the caller falls back to the raw text.

/** Codes the backend's error_catalog can emit. Keep in sync with
 *  `ALL_ERROR_CODES` in `backend/app/services/ai/error_catalog.py` — a code
 *  missing here degrades to the raw-error fallback rather than breaking.
 *
 *  该注释曾经在说谎:后端加了 6 个 `local_*` 码,这里一个都没加(终审 D-2)。没有
 *  跨语言的契约测试盯着这两份列表,所以「加码时两边都改」目前靠的是这条注释和
 *  下面那条测试——后者只能让**删除**变响,拦不住后端单方面新增。 */
export const AI_ERROR_CODES = [
  'PROVIDER_AUTH',
  'PROVIDER_RATE_LIMIT',
  'PROVIDER_QUOTA_CAP',
  'PROVIDER_UNREACHABLE',
  'PROVIDER_BAD_MODEL',
  'OUTPUT_PARSE',
  'TASK_TIMEOUT',
  'INTERNAL',
  // codex-local(本机 daemon)链路。小写是刻意的:后端 error_catalog 里这一族同时也是
  // HTTP/SSE 的字面 code(见 core/provider_errors._MAPPING),而那一面全 API 都是小写
  // ——一个字符串,一个含义。上面 8 个只走 task_tracking.metadata,所以保持大写。
  //
  // 这一族之所以必须在这里,是因为 issue 执行链上的 agent 也可以跑在本机模型上,
  // 那条路径的失败经 record_ai_error_code 落进 task_tracking.metadata.error_code,
  // 由本模块渲染——缺码不会崩,但会退回原始英文报错,把"去你自己电脑上做什么"这条
  // 唯一有用的信息丢掉。
  'local_daemon_offline',
  'local_daemon_outdated',
  'local_cli_missing',
  'local_tools_unsupported',
  'local_codex_not_logged_in',
  'local_ref_rejected',
  'local_codex_failed',
] as const;

export type AiErrorCode = (typeof AI_ERROR_CODES)[number];

/** English defaults, also the i18n fallback when a locale lacks the key. */
const DEFAULTS: Record<AiErrorCode, { title: string; hint: string }> = {
  PROVIDER_AUTH: {
    title: 'The AI provider rejected the credentials',
    hint: 'Open Settings → AI and check the API key for the model assigned to this task.',
  },
  PROVIDER_RATE_LIMIT: {
    title: 'The AI provider is rate-limiting',
    hint: 'Wait a moment and retry, or switch this task to a different model in Settings → AI.',
  },
  // Distinct from RATE_LIMIT on purpose: this is a cap configured on the
  // provider account, so "wait a moment" is wrong advice — it never clears
  // by itself. Backend keeps this ordered above the generic 429 rule.
  PROVIDER_QUOTA_CAP: {
    title: 'The provider account has hit its configured limit for this model',
    hint: 'Raise the limit in the provider console, or point this task at another model in Settings → AI.',
  },
  PROVIDER_UNREACHABLE: {
    title: "Couldn't reach the AI provider",
    hint: 'Check the provider endpoint in Settings → AI — if it is self-hosted, make sure it is running.',
  },
  PROVIDER_BAD_MODEL: {
    title: 'The configured model is unavailable',
    hint: 'The model name may be wrong or not enabled for this account. Pick another model in Settings → AI.',
  },
  OUTPUT_PARSE: {
    title: "The model's reply could not be used",
    hint: 'Retry — if it keeps happening, this model may not follow the required output format. Try a different one.',
  },
  TASK_TIMEOUT: {
    title: 'The task timed out',
    hint: 'Retry usually works. A smaller file or a faster model helps if it keeps timing out.',
  },
  INTERNAL: {
    title: 'Something went wrong on our side',
    hint: 'Retry — if it persists, the details are in Task Center.',
  },
  // codex-local: 修复动作全部落在**用户自己的电脑上**,所以 hint 指向那台机器上的
  // 一条具体命令或一个具体开关,而不是 "Settings → AI 换个模型"——对这条链路来说
  // 换模型是放弃,不是修复。
  local_daemon_offline: {
    title: 'Your local codex daemon was not connected',
    hint: 'Start it on your machine (Settings → AI → Local CLI shows how), then retry.',
  },
  local_daemon_outdated: {
    title: 'Your local codex daemon is out of date',
    hint: 'Re-run the install command from Settings → AI → Local CLI to update it, then retry.',
  },
  local_cli_missing: {
    title: 'The codex CLI is not installed on your machine',
    hint: 'Run `npm i -g @openai/codex` on that machine and restart the daemon.',
  },
  local_tools_unsupported: {
    title: 'Local Codex cannot run tools or Skills',
    hint: 'Unbind the Skills from this agent, or point it at a hosted model in Settings → AI.',
  },
  local_codex_not_logged_in: {
    title: 'Your local codex CLI is not logged in',
    hint: 'Run `codex login` on that machine, then retry.',
  },
  local_ref_rejected: {
    title: 'An attached image could not be used by your local daemon',
    hint: 'Attach images from your nous library and try again.',
  },
  local_codex_failed: {
    title: 'Local Codex did not produce a reply',
    hint: 'Check the daemon log on your machine, then retry.',
  },
};

export interface ResolvedTaskError {
  /** Short, user-facing headline. Never empty. */
  title: string;
  /** What the user can do about it. Absent when we only have raw text. */
  hint?: string;
  /** The structured code, when the backend supplied a known one. */
  code?: AiErrorCode;
}

/** i18next's `t` narrowed to what we use: key + English default. */
export type TranslateFn = (key: string, defaultValue: string) => string;

/** Raw engine text is unbounded (stack fragments, serialized JSON bodies);
 *  a toast that long is unreadable, so the fallback is clipped. */
const RAW_MAX = 160;

const IDENTITY_T: TranslateFn = (_key, defaultValue) => defaultValue;

function isKnownCode(value: unknown): value is AiErrorCode {
  return typeof value === 'string' && (AI_ERROR_CODES as readonly string[]).includes(value);
}

function truncate(s: string, max: number): string {
  const trimmed = s.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1)}…` : trimmed;
}

/**
 * Resolve a failed task into copy worth showing.
 *
 * @param metadata  `task_tracking.metadata` — the backend puts `error_code` here.
 * @param rawError  `task_tracking.error_msg`, used when there is no known code.
 * @param t         i18next `t`; omit to get the English defaults (tests, non-React callers).
 */
export function resolveTaskError(
  metadata: Record<string, unknown> | null | undefined,
  rawError?: string | null,
  t: TranslateFn = IDENTITY_T,
): ResolvedTaskError {
  const code = metadata?.error_code;

  if (isKnownCode(code)) {
    const fallback = DEFAULTS[code];
    return {
      code,
      title: t(`errors.${code}.title`, fallback.title),
      hint: t(`errors.${code}.hint`, fallback.hint),
    };
  }

  // No code (older row, unclassified failure, or a code this build doesn't
  // know): show the raw error rather than a generic message, so nothing the
  // engine reported is hidden from the user.
  const raw = (rawError || '').trim();
  if (raw) return { title: truncate(raw, RAW_MAX) };

  return { title: t('errors.UNKNOWN.title', 'The task failed') };
}

/** One-line form for toasts, which take a single string. */
export function formatTaskError(
  metadata: Record<string, unknown> | null | undefined,
  rawError?: string | null,
  t: TranslateFn = IDENTITY_T,
): string {
  const { title, hint } = resolveTaskError(metadata, rawError, t);
  return hint ? `${title} — ${hint}` : title;
}
