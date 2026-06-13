import React from 'react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import { getResourceCoverUrl } from '../../../services/resourceService';
import type { VisualAnalysisData } from '../../../services/aiService';

interface VisionResultBodyProps {
  task: UnifiedTask;
  data: unknown; // VisualAnalysisData
}

export const VisionResultBody: React.FC<VisionResultBodyProps> = ({ task, data }) => {
  const { t } = useTranslation();
  const d = (data ?? {}) as Partial<VisualAnalysisData>;
  const cover = task.resource_id ? getResourceCoverUrl(String(task.resource_id)) : null;
  const chips = [
    ...(d.objects ?? []),
    ...(d.scenes ?? []),
    ...(d.people ?? []),
  ].filter(Boolean);

  return (
    <div className="p-4 space-y-4">
      {cover && (
        <img src={cover} alt="" className="w-full max-h-56 object-contain rounded-lg bg-ink-950" />
      )}

      <div>
        <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">
          {t('topbar.visionDescription')}
        </div>
        {d.description ? (
          <div className="text-[13px] leading-relaxed text-ink-200 whitespace-pre-wrap break-words bg-ink-950/40 rounded p-3 border border-ink-800">
            {d.description}
          </div>
        ) : (
          <div className="text-xs text-ink-500">{t('topbar.noResultDetail')}</div>
        )}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c, i) => (
            <span key={i} className="px-2 py-0.5 rounded-full text-[10px] bg-ink-800 text-ink-300">
              {c}
            </span>
          ))}
        </div>
      )}

      {d.text && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">
            {t('topbar.visionText')}
          </div>
          <div className="text-xs text-ink-300 whitespace-pre-wrap break-words bg-ink-950/40 rounded p-2 border border-ink-800">
            {d.text}
          </div>
        </div>
      )}

      {(d.model || d.cost != null) && (
        <div className="flex flex-wrap gap-4 pt-1">
          {d.model && (
            <div className="flex flex-col">
              <span className="text-[10px] uppercase tracking-wide text-ink-500">{t('topbar.agentModel')}</span>
              <span className="text-sm font-semibold text-ink-100">{d.model}</span>
            </div>
          )}
          {d.cost != null && (
            <div className="flex flex-col">
              <span className="text-[10px] uppercase tracking-wide text-ink-500">cost</span>
              <span className="text-sm font-semibold text-ink-100">
                {d.cost}
                <span className="ml-0.5 text-[11px] text-ink-500">¢</span>
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
