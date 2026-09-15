/**
 * Retry a task IN PLACE — same row, next attempt.
 *
 * ── Why this is its own module ──────────────────────────────────────────────
 * Because the thing worth testing here is a ROUTING DECISION between two
 * endpoints that both "work", and it was wrong in production for months while
 * every layer's own tests stayed green.
 *
 * The user's report (2026-09-15, task-center screenshot): a failed douyin
 * parse retried four times produced four rows titled `(recovered)` while the
 * original sat untouched — "不还是新建任务？和之前的任务是剥离的？"
 *
 * `api_request_logs` settled it: all four clicks hit
 *   POST /api/v1/workflows/parse-8e1584e3…/restart   202
 * which is DBOS's `fork`. Fork means, by definition, "start a NEW workflow and
 * leave the original row in its terminal state". Nothing pre-creates a
 * task_tracking row for a forked id, so the running workflow fell into
 * `TaskManager.start()`'s self-heal and got an orphan row titled `(recovered)`
 * with no dedup_key, no flow_id and no link to the task being retried.
 *
 * `/task-manager/tasks/{id}/retry` is the endpoint that re-keys the EXISTING
 * row onto a new workflow and re-dispatches it. It is the one that matches
 * what "retry" means to the person clicking it, and the only one that can
 * count attempts (`metadata.retry_count`).
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { restartWorkflow } from './dbosWorkflowService';

/**
 * Reasons that make forking the right second choice.
 *
 * `retry_not_supported` is the backend saying "I have no dispatch branch for
 * this task type" — and its contract is that the row was NOT touched, which
 * is exactly what makes falling back safe. 404 means there is no
 * task_tracking row at all (an agent run, say), so in-place retry has nothing
 * to re-key.
 *
 * Everything else is a real failure and must surface. Forking past a 503
 * (media-parser switched off) would run the work the switch exists to stop,
 * and hand the user an orphan row as the receipt.
 */
function shouldFallBackToFork(status: number, reason: string): boolean {
  return status === 404 || (status === 409 && reason === 'retry_not_supported');
}

/** Typed reason out of the production error envelope (CLAUDE.md 2026-09-09). */
async function refusalReason(res: Response): Promise<string> {
  try {
    const body = await res.json();
    // `code` at the top level is only ever `http_409` — it cannot tell
    // retry_not_supported from any other conflict. The reason is in `details`.
    return body?.details?.code ?? body?.detail?.code ?? '';
  } catch {
    return ''; // non-JSON body (gateway error page) — decide on status alone
  }
}

export async function retryTaskInPlace(taskId: string): Promise<void> {
  const res = await fetch(
    `${getApiUrl()}/api/v1/task-manager/tasks/${taskId}/retry`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (res.ok) return;

  const reason = await refusalReason(res);
  if (!shouldFallBackToFork(res.status, reason)) {
    throw new Error(`retry ${res.status}${reason ? ` (${reason})` : ''}`);
  }
  await restartWorkflow(taskId);
}
