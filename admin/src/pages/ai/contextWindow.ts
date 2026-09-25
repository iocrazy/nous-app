// Context Window field of the Edit Model dialog: preset list, value ↔ choice
// mapping, validation and the PUT patch. Pure functions only — the dialog
// wires them to two form fields:
//   - `context_window_choice`: a preset value (as a string), CUSTOM or UNSET
//   - `context_window_tokens`: the free-form count, only read when CUSTOM

/** The column is int4; the backend rejects anything larger with a 422. */
export const INT4_MAX = 2_147_483_647

/** Choice sentinel: the admin types an arbitrary count. */
export const CUSTOM = 'custom'
/** Choice sentinel: no value — the runtime falls back to its builtin table / global default. */
export const UNSET = 'unset'

export interface ContextWindowPreset {
  label: string
  tokens: number
}

/**
 * Common provider windows. Binary sizes for most (131072 = "128K"), but 200K
 * is the decimal 200000 because that is what providers actually declare.
 */
export const CONTEXT_WINDOW_PRESETS: readonly ContextWindowPreset[] = [
  { label: '16K', tokens: 16_384 },
  { label: '32K', tokens: 32_768 },
  { label: '64K', tokens: 65_536 },
  { label: '128K', tokens: 131_072 },
  { label: '200K', tokens: 200_000 },
  { label: '256K', tokens: 262_144 },
  { label: '512K', tokens: 524_288 },
  { label: '1M', tokens: 1_048_576 },
]

/** Form values the dialog pre-fills for a row's current window. */
export interface ContextWindowFormValues {
  context_window_choice: string
  context_window_tokens: number | ''
}

/**
 * Current column value → form values.
 * - null / undefined / ≤0 → UNSET, custom input empty
 * - exactly a preset → that preset selected, custom input empty
 * - anything else → CUSTOM with the exact value in the custom input
 */
export function contextWindowFormValues(tokens: number | null | undefined): ContextWindowFormValues {
  if (!tokens || tokens <= 0) return { context_window_choice: UNSET, context_window_tokens: '' }
  const preset = CONTEXT_WINDOW_PRESETS.find((p) => p.tokens === tokens)
  if (preset) return { context_window_choice: String(preset.tokens), context_window_tokens: '' }
  return { context_window_choice: CUSTOM, context_window_tokens: tokens }
}

/**
 * Form values → the token count the admin chose, or null for "no value".
 * - UNSET / missing choice (e.g. type just switched to llm) → null
 * - CUSTOM → the custom input parsed; empty or non-positive → null
 * - a preset string → its number
 */
export function resolveContextWindow(choice: unknown, custom: unknown): number | null {
  if (choice === undefined || choice === null || choice === '' || choice === UNSET) return null
  const raw = choice === CUSTOM ? custom : choice
  const text = raw === undefined || raw === null ? '' : String(raw).trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) && n > 0 ? n : null
}

/**
 * Arco validator for the custom input. Empty passes here — the dialog adds a
 * `required` rule while CUSTOM is selected; otherwise a positive integer ≤ int4.
 */
export function validateContextWindow(value: unknown, callback: (error?: string) => void): void {
  const raw = value === undefined || value === null ? '' : String(value).trim()
  if (raw === '') return callback()
  const n = Number(raw)
  if (!Number.isInteger(n) || n <= 0) return callback('Must be a positive whole number of tokens')
  if (n > INT4_MAX) return callback(`Must be at most ${INT4_MAX.toLocaleString('en-US')} tokens`)
  callback()
}

/**
 * The PUT fragment for the window:
 * - type is not llm → {} (non-LLM rows have no window; leave the column alone)
 * - a chosen count > 0 → { context_window_tokens: n }
 * - no value and the row had one → { clear_context_window: true } (the PUT
 *   drops nulls, so clearing needs its own flag)
 * - no value and the row had none → {}
 */
export function contextWindowPatch(
  currentTokens: number | null | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  if (values.type !== 'llm') return {}
  const n = resolveContextWindow(values.context_window_choice, values.context_window_tokens)
  if (n !== null) return { context_window_tokens: n }
  return currentTokens ? { clear_context_window: true } : {}
}
