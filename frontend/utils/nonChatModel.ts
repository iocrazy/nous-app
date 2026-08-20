// frontend/utils/nonChatModel.ts
//
// "Does this BYOK model id look like something that CANNOT hold a chat?"
//
// Production incident (2026-08-16): a user pruned the doubao provider card's
// enabled-models list down to `doubao-embedding-vision-251215`. Summarization
// resolves its model as "the first keyed+enabled provider in
// doubao → qwen → openai → deepseek, then THAT provider's selected_model"
// (backend/app/services/ai/providers/ai_provider_helpers.py), so every summary
// afterwards POSTed an embedding model to /chat/completions and failed. The
// settings page said nothing; the first sign was a failed task.
//
// ── Why a name heuristic and not a type field ───────────────────────────────
// A BYOK catalog is whatever the provider's own `GET /models` returned — raw
// upstream model ids, no type/capability metadata (see AISettings'
// `config.models`, populated by Test Connection). There is no authoritative
// signal to read, so the id string is all we have. That makes this module a
// GUESS, and it is built to be wrong in only one direction:
//
//   - Match only on whole tokens of an unambiguous vocabulary. `embedding`,
//     `seedream`, `tts` mean one thing; `vision`, `audio`, `realtime` do NOT
//     (`doubao-vision-pro` and `gpt-4o-audio-preview` are chat models), so
//     those are deliberately absent.
//   - Anything unmatched returns null — "unknown", never "fine" and never
//     "broken". A wrong red line on a working model teaches the user to ignore
//     red lines, which is the failure this guard exists to prevent, not a
//     smaller version of it (same rule as utils/modelHealth's never-probed
//     models).
//
// Deliberately NOT here: any notion of blocking. The caller warns; the user
// still decides — consistent with the platform-model health line (#1838).
//
// Deliberately NOT here: the wording. Callers translate; this module returns a
// kind so it stays importable without the i18n instance.

/** The families of model that a chat/completions call cannot use. */
export type NonChatKind = 'embedding' | 'rerank' | 'image' | 'video' | 'speech';

/**
 * Whole-token vocabulary per kind, in match precedence order.
 *
 * The bar for an entry is AMBIGUITY, not length: `bge` / `asr` / `t2i` are
 * three characters and mean exactly one thing, so they are in; `sd` and `gte`
 * are the same length and mean several things, so they are out. Notable
 * exclusions, each with a real counter-example:
 *   `vision`  — doubao/qwen ship vision-capable CHAT models (`doubao-vision-pro`).
 *   `audio`   — `gpt-4o-audio-preview` is a chat-completions model.
 *   `realtime`— `gpt-4o-realtime-preview` likewise.
 *   `voice`   — `glm-4-voice` is an end-to-end speech CHAT model, and the real
 *               speech models spell it as one word (`cosyvoice`, `sensevoice`),
 *               which never yields a bare `voice` token. It caught only the
 *               wrong thing, so it is gone.
 *   `video`   — `video-llava-7b` / `Video-LLaMA-2-7B` are video-UNDERSTANDING
 *               chat models, routinely served over a vLLM OpenAI-compatible
 *               endpoint, i.e. exactly what a BYOK `base_url` points at.
 *               `seedance` / `sora` / `t2v` / `i2v` already cover the
 *               mainstream text-to-video naming, so the bare word was pure
 *               downside.
 *   `ocr`     — several providers expose OCR through chat-completions.
 *
 * `image` is kept, with one known wrinkle: the platform catalog has a row named
 * `codex-image` whose `actual_model` is the chat model `gpt-5.4` (migration
 * 430). That is a platform ALIAS though — a BYOK catalog carries the upstream
 * id, where `image` remains a reliable tell.
 */
const KIND_TOKENS: ReadonlyArray<readonly [NonChatKind, readonly string[]]> = [
  ['embedding', ['embedding', 'embeddings', 'embed', 'bge']],
  ['rerank', ['rerank', 'reranker']],
  [
    'image',
    ['seedream', 'dalle', 'dall', 't2i', 'i2i', 'imagen', 'image', 'flux', 'sdxl'],
  ],
  ['video', ['seedance', 'sora', 't2v', 'i2v']],
  ['speech', ['tts', 'asr', 'whisper', 'speech', 'transcribe', 'transcription']],
];

/**
 * Split a model id into lowercase alphanumeric tokens.
 *
 * Token-wise, not substring: `doubao-embedding-vision-251215` →
 * ['doubao','embedding','vision','251215'] hits `embedding`, while a
 * hypothetical `attsu-chat` never hits `tts`. Digits stay attached to letters
 * (`gpt4o` is one token) because splitting them would manufacture matches from
 * version numbers.
 */
function tokenize(modelId: string): string[] {
  return modelId.toLowerCase().split(/[^a-z0-9]+/i).filter(Boolean);
}

/**
 * The non-chat family this model id looks like, or `null` when nothing in the
 * vocabulary matches (which includes every ordinary chat model, and every
 * model whose id simply doesn't say).
 *
 * The real incident case: `doubao-embedding-vision-251215` → `'embedding'`.
 */
export function suspectedNonChatKind(
  modelId: string | null | undefined,
): NonChatKind | null {
  if (!modelId) return null;
  const tokens = new Set(tokenize(modelId));
  for (const [kind, vocabulary] of KIND_TOKENS) {
    if (vocabulary.some((word) => tokens.has(word))) return kind;
  }
  return null;
}

/** i18n key for a kind's noun phrase ("an embedding model" / "向量模型"). */
export function nonChatKindKey(kind: NonChatKind): string {
  return `aiSettings.nonChatKind.${kind}`;
}
