import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { taskErrorCopy } from '../../utils/taskErrorCopy';
import { useTaskResult } from './useTaskResult';
import { MediaResultBody } from './bodies/MediaResultBody';
import { AgentResultBody } from './bodies/AgentResultBody';
import { TextResultBody } from './bodies/TextResultBody';
import { VisionResultBody } from './bodies/VisionResultBody';
import { CanvasGenResultBody } from './bodies/CanvasGenResultBody';
import { CoverFramesResultBody } from './bodies/CoverFramesResultBody';

interface TaskDetailModalProps {
  task: UnifiedTask | null;
  onClose: () => void;
  onOpenResource: (resourceId: string) => void;
}

export const TaskDetailModal: React.FC<TaskDetailModalProps> = ({
  task,
  onClose,
  onOpenResource,
}) => {
  const { t } = useTranslation();
  const result = useTaskResult(task);

  useEffect(() => {
    if (!task) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [task, onClose]);

  if (!task) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[80vh] flex flex-col bg-ink-900 border border-ink-700/60 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-ink-800 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-ink-100 truncate">{task.title}</div>
            <div className="text-[11px] text-ink-500">
              {taskTypeLabel(task.task_type)} · {task.status}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-ink-500 hover:text-ink-200 hover:bg-ink-800 transition-colors shrink-0"
            aria-label={t('common.close')}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {/* Above the loading / fetch-error branches: a failed task's reason
              must not wait on (or be replaced by) a result fetch that is
              looking for output the task never produced. */}
          <TaskErrorBlock task={task} />
          {result.loading && (
            <div className="flex items-center justify-center py-12 text-ink-500">
              <div className="w-5 h-5 border-2 border-ink-600 border-t-indigo-400 rounded-full animate-spin" />
            </div>
          )}
          {!result.loading && result.error && (
            <div className="p-4 text-xs text-red-400">{result.error}</div>
          )}
          {!result.loading && !result.error && (
            <>
              {result.kind === 'media' && (
                <MediaResultBody task={task} resource={result.data} onOpenResource={onOpenResource} />
              )}
              {result.kind === 'agent' && <AgentResultBody task={task} />}
              {(result.kind === 'transcript' || result.kind === 'summary') && (
                <TextResultBody kind={result.kind} data={result.data} />
              )}
              {result.kind === 'vision' && <VisionResultBody task={task} data={result.data} />}
              {result.kind === 'canvasGen' && <CanvasGenResultBody task={task} />}
              {result.kind === 'coverFrames' && <CoverFramesResultBody task={task} />}
              {result.kind === 'generic' && <GenericResultBody task={task} />}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

/**
 * Why this sits at the modal's body level and not inside a result body:
 * `useTaskResult` routes an ai_summary task to `TextResultBody` whenever it
 * has a `resource_id` (all four rows of the 2026-08-19 incident did), and
 * none of the per-kind bodies read `error_msg`. So a failed task rendered
 * either a fetch error for a summary that was never produced, or plain
 * "No result detail" — the reason was in the row the whole time and no body
 * was looking at it. Hoisting it here also means a body added later cannot
 * silently reintroduce the gap.
 */
const TaskErrorBlock: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  if (!task.error_msg) return null;

  const { message, hint, detail } = taskErrorCopy(task.metadata, task.error_msg, t);
  const raw = task.error_msg.trim();
  const showRaw = raw.length > 0 && raw !== message;

  return (
    <div className="px-4 pt-4 text-xs text-red-400">
      <div data-testid="task-error-message" className="whitespace-pre-wrap break-words">{message}</div>
      {hint && <p className="mt-0.5 text-red-300/70">{hint}</p>}
      {/* Not inside the <details> below. This is the failing party's own
          explanation written FOR the user — for a content refusal it names
          what was objected to and hands back a working rewrite — so it is the
          most useful thing on screen, not the technical overflow. Burying it
          under a collapsed disclosure is the 2026-09-04 bug. */}
      {detail && (
        <pre
          data-testid="task-error-detail"
          className="mt-1.5 text-[11px] text-ink-300 whitespace-pre-wrap break-words bg-ink-950/60 border border-ink-700/60 rounded p-2 max-h-56 overflow-y-auto font-sans"
        >
          {detail}
        </pre>
      )}
      {showRaw && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-red-300/60 hover:text-red-300">
            Details
          </summary>
          <pre className="mt-1 text-[10px] text-red-300/70 whitespace-pre-wrap break-words bg-red-500/5 border border-red-500/15 rounded p-2 max-h-40 overflow-y-auto">
            {raw}
          </pre>
        </details>
      )}
    </div>
  );
};

const GenericResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const hasMeta = task.metadata && Object.keys(task.metadata).length > 0;
  return (
    <div className="p-4 space-y-3">
      {hasMeta ? (
        <pre className="text-[11px] text-ink-400 font-mono whitespace-pre-wrap break-words bg-ink-950/50 rounded p-3">
          {JSON.stringify(task.metadata, null, 2)}
        </pre>
      ) : (
        <div className="text-xs text-ink-500">{t('topbar.noResultDetail')}</div>
      )}
    </div>
  );
};
