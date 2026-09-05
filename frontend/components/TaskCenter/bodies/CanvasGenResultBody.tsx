/**
 * CanvasGenResultBody — detail body for smart-canvas generation tasks
 * (task_type canvas_gen, ②-5). Everything renders from task metadata: the
 * durable result_url (image or video preview) plus an Open Canvas jump —
 * no resource row exists for these results.
 */

import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import { getApiUrl } from '../../../utils/apiConfig';

/** `result_url` is API-relative (`/api/v1/generated-media/…`); on the deployed
 *  site the app and the API are different hosts, so a bare relative src
 *  resolved against the app host and 404'd — a broken image where the result
 *  should be (2026-08-29). */
function absolute(url: string): string {
  return url.startsWith('/') ? `${getApiUrl()}${url}` : url;
}

export function CanvasGenResultBody({ task }: { task: UnifiedTask }) {
  const meta = (task.metadata ?? {}) as {
    result_url?: string;
    canvas_id?: string;
    kind?: string;
  };

  return (
    // The prompt and item counter moved to the modal-level TaskDescriptionBlock
    // (above the outcome); this body is the OUTCOME only.
    <div className="p-4 space-y-3">
      {meta.result_url ? (
        meta.kind === 'video' ? (
          <video
            src={absolute(meta.result_url)}
            controls
            className="max-h-72 w-full rounded-lg bg-black object-contain"
          />
        ) : (
          <img
            src={absolute(meta.result_url)}
            alt={task.subtitle || 'Generated result'}
            className="max-h-72 w-full rounded-lg object-contain"
          />
        )
      ) : (
        <div className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-xs text-ink-500">
          {task.status === 'completed'
            ? 'No result attached'
            : task.status === 'failed' || task.status === 'cancelled'
              ? 'No image was produced.'
              : 'Result appears here when the generation finishes'}
        </div>
      )}
      {meta.canvas_id && (
        <a
          href={`/canvas/${meta.canvas_id}`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-xs text-ink-200 hover:border-ink-500"
        >
          Open Canvas
        </a>
      )}
    </div>
  );
}
