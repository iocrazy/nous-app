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
 * Every entry must be a token that no chat model legitimately carries. When in
 * doubt, leave it out — see the module header. Notable exclusions and why:
 *   `vision`  — doubao/qwen ship vision-capable CHAT models (`doubao-vision-pro`).
 *   `audio`   — `gpt-4o-audio-preview` is a chat-completions model.
 *   `sd`/`gte`/`veo` — too short to be safely distinctive across providers.
 *   `ocr`     — several providers expose OCR through chat-completions.
 */
const KIND_TOKENS: ReadonlyArray<readonly [NonChatKind, readonly string[]]> = [
  ['embedding', ['embedding', 'embeddings', 'embed', 'bge']],
  ['rerank', ['rerank', 'reranker']],
  [
    'image',
    ['seedream', 'dalle', 'dall', 't2i', 'i2i', 'imagen', 'image', 'flux', 'sdxl'],
  ],
  ['video', ['seedance', 'sora', 'video', 't2v', 'i2v']],
  [
    'speech',
    ['tts', 'asr', 'whisper', 'speech', 'transcribe', 'transcription', 'voice'],
  ],
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
