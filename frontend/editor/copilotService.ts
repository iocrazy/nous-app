/**
 * Copilot structured-edit helpers (spec v3 §3.2 / §3.4).
 *
 * Two layers live here:
 *  - Phase-1 LOCAL transforms (Polish format): deterministic, DOM-free pure
 *    functions dispatched through the owning scene's op queue.
 *  - Phase-2 free-text reconciler (`requestCopilotOps`): POSTs an instruction to
 *    the backend, which returns anchor-based ops already dry-run-validated. The
 *    caller applies them through the SAME If-Match op queue (actor = user token),
 *    so there is zero new concurrency surface. The endpoint is flag-gated
 *    (FEATURE_COPILOT_OPS); a 404 means the flag is off — surfaced as
 *    CopilotDisabledError so the card can degrade the free-text box.
 */
import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from '../services/parserService';
import { unwrapResponse } from '../utils/apiHelpers';
import { OpRejectedError } from './sceneService';
import type { ElementOp, ElementType, ScriptElement } from './types';

// 422 (OpRejectedError) reuses sceneService's typed error so the card + queue
// treat a rejected copilot op exactly like a rejected manual op.
export { OpRejectedError } from './sceneService';

const apiBase = () => `${getApiUrl()}/api/v1`;

/** 404 from copilot-ops: FEATURE_COPILOT_OPS is off — the endpoint is hidden. */
export class CopilotDisabledError extends Error {
  constructor() {
    super('copilot_ops_disabled');
    this.name = 'CopilotDisabledError';
  }
}

/**
 * Reconciler result. `proposal` is present+true when the scene advanced past the
 * `read_version` we sent (spec v3 §2.2 stale handling): the server regenerated
 * the ops against the CURRENT elements and `base_version` is that current
 * version — the editor must let the user review before applying.
 */
export interface CopilotOpsResult {
  ops: ElementOp[];
  base_version: number;
  summary: string;
  proposal?: boolean;
}

/**
 * Ask the backend to turn a free-text `instruction` into element ops for the
 * scene, generated against `readVersion`. Resolves with dry-run-validated ops;
 * throws OpRejectedError on 422 (the LLM could not produce applyable ops) and
 * CopilotDisabledError on 404 (flag off).
 */
export async function requestCopilotOps(
  sceneId: string,
  instruction: string,
  readVersion: number,
): Promise<CopilotOpsResult> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/copilot-ops`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction, read_version: readVersion }),
  });
  if (res.status === 404) throw new CopilotDisabledError();
  if (res.status === 422) {
    const body = await res.json();
    throw new OpRejectedError(body.code, body.detail);
  }
  return unwrapResponse<CopilotOpsResult>(res);
}

/**
 * "Polish format" for one element's text: trim the ends and collapse internal
 * whitespace runs to a single space; character cues are additionally upcased
 * (screenplay convention). Returns the cleaned text (may equal the input).
 */
export function polishText(text: string, type: ElementType): string {
  const cleaned = text.trim().replace(/\s+/g, ' ');
  return type === 'character' ? cleaned.toUpperCase() : cleaned;
}

/**
 * Build the update ops that polish the selected elements. Only elements whose
 * text actually changes produce an op — a no-op selection yields an empty batch
 * (nothing dispatched, nothing to undo).
 */
export function buildPolishOps(elements: ScriptElement[], selectedIds: string[]): ElementOp[] {
  const selected = new Set(selectedIds);
  const ops: ElementOp[] = [];
  for (const el of elements) {
    if (!selected.has(el.id)) continue;
    const next = polishText(el.text, el.type);
    if (next !== el.text) {
      ops.push({ op: 'update', element_id: el.id, payload: { text: next } });
    }
  }
  return ops;
}
