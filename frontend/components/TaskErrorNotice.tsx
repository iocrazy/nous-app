import React from 'react';
import { AlertCircle } from 'lucide-react';
import { humanizeTaskError } from '../utils/humanizeTaskError';

// Renders a task/AI failure as user-facing copy: a plain-English message, an
// optional actionable hint, and a collapsible "Details" holding the raw
// exception string (so nothing is lost). Used anywhere a raw error_msg would
// otherwise face the user (media detail AI tabs, Task Center expanded rows).
//
// The raw string is only shown under <details> when it differs from the
// friendly message — for already-clean errors there is nothing extra to reveal.

interface TaskErrorNoticeProps {
  error?: string | null;
  /** 'sm' for the primary Transcribe error, 'xs' for the denser summary tabs. */
  size?: 'sm' | 'xs';
  className?: string;
}

export const TaskErrorNotice: React.FC<TaskErrorNoticeProps> = ({
  error,
  size = 'sm',
  className = '',
}) => {
  if (!error) return null;
  const { message, hint } = humanizeTaskError(error);
  const raw = error.trim();
  const showRaw = raw.length > 0 && raw !== message;
  const iconSize = size === 'sm' ? 14 : 12;
  const textCls = size === 'sm' ? 'text-sm' : 'text-xs';

  return (
    <div className={`text-red-400 ${textCls} ${className}`}>
      <div className="flex items-center gap-2">
        <AlertCircle size={iconSize} className="flex-shrink-0" />
        <span>{message}</span>
      </div>
      {hint && <p className="mt-1 text-red-300/80 text-xs pl-[calc(0.5rem+14px)]">{hint}</p>}
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

export default TaskErrorNotice;
