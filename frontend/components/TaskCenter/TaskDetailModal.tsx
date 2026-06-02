import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { useTaskResult } from './useTaskResult';
import { MediaResultBody } from './bodies/MediaResultBody';
import { AgentResultBody } from './bodies/AgentResultBody';
import { TextResultBody } from './bodies/TextResultBody';

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
        className="w-full max-w-2xl max-h-[80vh] flex flex-col bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-zinc-800 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-zinc-100 truncate">{task.title}</div>
            <div className="text-[11px] text-zinc-500">
              {taskTypeLabel(task.task_type)} · {task.status}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors shrink-0"
            aria-label={t('common.close')}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {result.loading && (
            <div className="flex items-center justify-center py-12 text-zinc-500">
              <div className="w-5 h-5 border-2 border-zinc-600 border-t-indigo-400 rounded-full animate-spin" />
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
              {result.kind === 'generic' && <GenericResultBody task={task} />}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

const GenericResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const hasMeta = task.metadata && Object.keys(task.metadata).length > 0;
  return (
    <div className="p-4 space-y-3">
      {task.error_msg && (
        <div className="text-xs text-red-400 whitespace-pre-wrap break-words">{task.error_msg}</div>
      )}
      {hasMeta ? (
        <pre className="text-[11px] text-zinc-400 font-mono whitespace-pre-wrap break-words bg-zinc-950/50 rounded p-3">
          {JSON.stringify(task.metadata, null, 2)}
        </pre>
      ) : (
        <div className="text-xs text-zinc-500">{t('topbar.noResultDetail')}</div>
      )}
    </div>
  );
};
