import React from 'react';
import { useTranslation } from 'react-i18next';

interface TextResultBodyProps {
  kind: 'transcript' | 'summary';
  /** TranscriptData ({text,...}) or SummaryData ({summary,key_points,topics}). */
  data: unknown;
}

export const TextResultBody: React.FC<TextResultBodyProps> = ({ kind, data }) => {
  const { t } = useTranslation();
  const d = (data ?? {}) as {
    text?: string;
    summary?: string;
    key_points?: string[];
    topics?: string[];
  };
  const body = kind === 'transcript' ? d.text : d.summary;

  return (
    <div className="p-4 space-y-3">
      {body ? (
        <div className="text-[13px] leading-relaxed text-ink-200 whitespace-pre-wrap break-words bg-ink-950/40 rounded p-3 border border-ink-800">
          {body}
        </div>
      ) : (
        <div className="text-xs text-ink-500">{t('topbar.noResultDetail')}</div>
      )}

      {kind === 'summary' && Array.isArray(d.key_points) && d.key_points.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">
            {t('topbar.keyPoints')}
          </div>
          <ul className="list-disc list-inside space-y-1 text-xs text-ink-300">
            {d.key_points.map((kp, i) => (
              <li key={i}>{kp}</li>
            ))}
          </ul>
        </div>
      )}

      {kind === 'summary' && Array.isArray(d.topics) && d.topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {d.topics.map((tp, i) => (
            <span key={i} className="px-2 py-0.5 rounded-full text-[10px] bg-ink-800 text-ink-300">
              {tp}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};
