/**
 * One typed question, three wire shapes (phase 2a §1):
 *
 *   issue marker   — `issues.execution_state.awaiting_input`
 *                    (input_gate.build_awaiting_marker: prompt/since/issue_id +
 *                    question_id/kind/options/allow_free_text/run_id, and
 *                    `answered_at` once answered)
 *   run view       — `agent_runs.metadata_json.view.question`
 *                    (folds/question.py: id/kind/prompt/options/allow_free_text/asked_at)
 *   chat metadata  — assistant `metadata_json.awaiting_input`
 *                    (Task 4: Question.to_payload() + run_id + `answered{value,at,superseded}`)
 *
 * Everything the UI renders reads through these parsers, so a backend field
 * rename is one edit. The option LABEL is the answer value the backend
 * validates against (`answer_matches`) — never index, never a slug.
 */

export interface QuestionOption {
  label: string;
  description?: string;
}

export interface TypedQuestion {
  id: string;
  kind: string;
  prompt: string;
  options: QuestionOption[];
  allowFreeText: boolean;
  /** Present once answered (or superseded by a later plain message). `value`
   *  is null when the wire shape carries no value (a marker stamped before
   *  `answered_value` existed). */
  answered?: { value: string | null; superseded?: boolean };
}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function options(raw: unknown): QuestionOption[] {
  if (!Array.isArray(raw)) return [];
  const out: QuestionOption[] = [];
  for (const o of raw) {
    if (!isRecord(o) || typeof o.label !== 'string') continue;
    out.push({ label: o.label, description: typeof o.description === 'string' ? o.description : undefined });
  }
  return out;
}

/** The common core; `idKey` names the id field of the shape at hand. */
function core(raw: unknown, idKey: 'question_id' | 'id'): TypedQuestion | null {
  if (!isRecord(raw)) return null;
  const id = raw[idKey];
  if (typeof id !== 'string' || !id) return null;
  return {
    id,
    kind: typeof raw.kind === 'string' && raw.kind ? raw.kind : 'user',
    prompt: typeof raw.prompt === 'string' ? raw.prompt : '',
    options: options(raw.options),
    allowFreeText: raw.allow_free_text !== false,
  };
}

/** Issue detail / needs-input feed: the parked marker in `execution_state`. */
export function questionFromMarker(execState: unknown): TypedQuestion | null {
  if (!isRecord(execState)) return null;
  const marker = execState.awaiting_input;
  const q = core(marker, 'question_id');
  if (!q) return null;
  if (isRecord(marker) && marker.answered_at) {
    return {
      ...q,
      answered: { value: typeof marker.answered_value === 'string' ? marker.answered_value : null },
    };
  }
  return q;
}

/** Cockpit: the current run's folded view (`view.question`, cleared on answer). */
export function questionFromRunView(view: unknown): TypedQuestion | null {
  if (!isRecord(view)) return null;
  return core(view.question, 'id');
}

/** Chat: the assistant message's `metadata_json.awaiting_input` (Task 4). */
export function questionFromChatMetadata(meta: unknown): TypedQuestion | null {
  if (!isRecord(meta)) return null;
  const raw = meta.awaiting_input;
  const q = core(raw, 'question_id');
  if (!q) return null;
  const answered = isRecord(raw) && isRecord(raw.answered) ? raw.answered : null;
  if (!answered) return q;
  return {
    ...q,
    answered: {
      value: typeof answered.value === 'string' ? answered.value : null,
      superseded: answered.superseded === true,
    },
  };
}

/** Feed row (`NeedsInputItem`): the marker's fields flattened onto the item. */
export function questionFromNeedsInputItem(item: {
  question_id?: string | null;
  kind?: string | null;
  question?: string | null;
  options?: unknown;
  allow_free_text?: boolean;
}): TypedQuestion | null {
  if (!item.question_id) return null;
  return {
    id: item.question_id,
    kind: item.kind || 'user',
    prompt: item.question ?? '',
    options: options(item.options),
    allowFreeText: item.allow_free_text !== false,
  };
}

/** The copy for a rejected answer: a typed code maps to `question.error.<code>`
 *  (falling back to the server message), anything else is shown as-is. */
export function answerErrorText(
  err: unknown,
  t: (key: string, fallback: string) => string,
): string {
  const code = (err as { code?: unknown } | null)?.code;
  const message = err instanceof Error ? err.message : String(err);
  if (typeof code === 'string' && code) return t(`question.error.${code}`, message || code);
  return message;
}
