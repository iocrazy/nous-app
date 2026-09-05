/**
 * Fallback result body: the task's own summary / error, for kinds that have
 * no typed payload yet. (Moved out of TaskDetailModal when the bodies became
 * a registry.)
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

export const GenericResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
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
