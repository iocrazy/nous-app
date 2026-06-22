// frontend/components/TopicInspiration/HotspotCard.tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Star, EyeOff } from 'lucide-react';
import type { Hotspot } from '../../services/topicService';
import { islandUI } from '../../utils/featureFlags';

interface Props {
  hotspot: Hotspot;
  onSelect: (h: Hotspot) => void;
  selected?: boolean;
  onToggleSave?: (h: Hotspot) => void;
  onToggleHide?: (h: Hotspot) => void;
}

export const HotspotCard: React.FC<Props> = ({
  hotspot,
  onSelect,
  selected,
  onToggleSave,
  onToggleHide,
}) => {
  const { t } = useTranslation();
  const island = islandUI();
  const isPick = typeof hotspot.score === 'number' && hotspot.score >= 0.8;
  const read = !!hotspot.is_read;

  const bgClass = island ? 'bg-island' : 'bg-ink-900';
  const borderClass = selected
    ? 'border-[rgba(99,102,241,.35)] shadow-[0_0_0_2px_rgba(99,102,241,.12)]'
    : island
    ? 'border-line-strong hover:border-accent/40'
    : 'border-ink-800 hover:border-ink-600';

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(hotspot);
    }
  };

  const iconBtn = `p-1 rounded-md transition-colors ${
    island ? 'hover:bg-island-2 text-content-4' : 'hover:bg-ink-800 text-ink-500'
  }`;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(hotspot)}
      onKeyDown={onKeyDown}
      className={`group w-full text-left rounded-[14px] border px-4 py-3.5 transition-colors shadow-sm cursor-pointer ${bgClass} ${borderClass} ${
        read ? 'opacity-60' : ''
      }`}
    >
      {/* Header row: source (left) + pick badge + score + actions (right) */}
      <div className="flex items-center justify-between">
        <span className={`text-[12px] ${island ? 'text-content-4' : 'text-ink-500'}`}>
          {hotspot.source_label}
        </span>
        <span className="flex items-center gap-1.5">
          {isPick && (
            <span
              className="text-[11px] font-bold rounded-full px-2 py-0.5"
              style={{
                color: 'var(--amb-tx, #b45309)',
                background: 'var(--amb-bg, rgba(245,158,11,.12))',
                border: '1px solid var(--amb-bd, rgba(245,158,11,.35))',
              }}
            >
              {t('topic.pick', 'Pick')}
            </span>
          )}
          {typeof hotspot.score === 'number' && (
            <span
              className="text-[12px] font-bold rounded-full px-2 py-0.5 bg-amber-500/10"
              style={{ color: 'var(--amb-tx, #b45309)' }}
            >
              {Math.round(hotspot.score * 100)}
            </span>
          )}
          {onToggleSave && (
            <button
              type="button"
              aria-label={t('topic.save', 'Save')}
              title={t('topic.save', 'Save')}
              onClick={(e) => {
                e.stopPropagation();
                onToggleSave(hotspot);
              }}
              className={iconBtn}
            >
              <Star
                size={15}
                className={hotspot.is_saved ? 'text-amber-500' : ''}
                fill={hotspot.is_saved ? 'currentColor' : 'none'}
              />
            </button>
          )}
          {onToggleHide && (
            <button
              type="button"
              aria-label={hotspot.is_hidden ? t('topic.unhide', 'Unhide') : t('topic.hide', 'Hide')}
              title={hotspot.is_hidden ? t('topic.unhide', 'Unhide') : t('topic.hide', 'Hide')}
              onClick={(e) => {
                e.stopPropagation();
                onToggleHide(hotspot);
              }}
              className={`${iconBtn} ${
                hotspot.is_hidden ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'
              }`}
            >
              <EyeOff size={15} className={hotspot.is_hidden ? 'text-accent' : ''} />
            </button>
          )}
        </span>
      </div>

      {/* Title */}
      <h4
        className={`text-[15px] font-semibold mt-1.5 mb-1 ${
          island ? 'text-content' : 'text-ink-100'
        }`}
      >
        {hotspot.title}
      </h4>

      {/* Summary */}
      {(hotspot.ai_summary || hotspot.summary) && (
        <p
          className={`text-[13px] leading-relaxed ${
            island ? 'text-content-2' : 'text-ink-300'
          }`}
        >
          {hotspot.ai_summary || hotspot.summary}
        </p>
      )}

      {/* Tags */}
      {(hotspot.tags?.length ?? 0) > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {(hotspot.tags ?? []).map((tag) => (
            <span
              key={tag}
              className={`text-[10px] px-2 py-0.5 rounded-full ${
                island ? 'bg-island-2 text-content-3' : 'bg-ink-800 text-ink-400'
              }`}
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Reason callout */}
      {hotspot.reason && (
        <div className="topic-reason-callout mt-2.5 rounded-lg border-l-[3px] border-emerald-500 bg-emerald-500/[0.07] px-3 py-2 text-[12px] text-[#0f766e]">
          <span className="font-bold block mb-0.5">{t('topic.reason', 'Why it matters')}</span>
          {hotspot.reason}
        </div>
      )}
    </div>
  );
};
