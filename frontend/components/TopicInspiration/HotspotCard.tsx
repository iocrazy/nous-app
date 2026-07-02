// frontend/components/TopicInspiration/HotspotCard.tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Star, EyeOff } from 'lucide-react';
import type { Hotspot } from '../../services/topicService';

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
  const isPick = typeof hotspot.score === 'number' && hotspot.score >= 0.8;
  const read = !!hotspot.is_read;

  const bgClass = 'bg-island';
  const borderClass = selected
    ? 'border-[rgba(99,102,241,.35)] shadow-[0_0_0_2px_rgba(99,102,241,.12)]'
    : 'border-line-strong hover:border-accent/40';

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(hotspot);
    }
  };

  const iconBtn = `p-1 rounded-md transition-colors hover:bg-island-2 text-content-4`;

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
      {/* Header row: source + board rank (left) + pick/score + actions (right) */}
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 min-w-0">
          <span className={`text-[12px] truncate text-content-4`}>
            {hotspot.source_label}
          </span>
          {typeof hotspot.best_rank === 'number' && hotspot.best_rank <= 10 && (
            <span
              className="text-[10px] font-bold rounded px-1.5 py-0.5 shrink-0"
              title={t('topic.bestRankHint', 'Peak board position')}
              style={{
                color: 'var(--amb-tx, #b45309)',
                background: 'var(--amb-bg, rgba(245,158,11,.12))',
              }}
            >
              #{hotspot.best_rank}
            </span>
          )}
          {typeof hotspot.source_count === 'number' && hotspot.source_count > 1 && (
            <span
              className="text-[10px] font-bold rounded px-1.5 py-0.5 shrink-0"
              title={t('topic.crossPlatformHint', 'Trending across multiple platforms')}
              style={{
                color: 'var(--ind-tx, #4338ca)',
                background: 'var(--ind-bg, rgba(99,102,241,.12))',
              }}
            >
              {t('topic.platforms', { count: hotspot.source_count, defaultValue: '{{count}} platforms' })}
            </span>
          )}
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
        className={`text-[15px] font-semibold mt-1.5 mb-1 text-content`}
      >
        {hotspot.title}
      </h4>

      {/* Summary */}
      {(hotspot.ai_summary || hotspot.summary) && (
        <p
          className={`text-[13px] leading-relaxed text-content-2`}
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
              className={`text-[10px] px-2 py-0.5 rounded-full bg-island-2 text-content-3`}
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
