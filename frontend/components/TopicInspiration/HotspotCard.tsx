// frontend/components/TopicInspiration/HotspotCard.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { islandUI } from '../../utils/featureFlags';

interface Props {
  hotspot: Hotspot;
  onSelect: (h: Hotspot) => void;
  selected?: boolean;
}

export const HotspotCard: React.FC<Props> = ({ hotspot, onSelect, selected }) => {
  const island = islandUI();
  const isPick = typeof hotspot.score === 'number' && hotspot.score >= 0.8;

  const bgClass = island ? 'bg-island' : 'bg-ink-900';
  const borderClass = selected
    ? 'border-[rgba(99,102,241,.35)] shadow-[0_0_0_2px_rgba(99,102,241,.12)]'
    : island
    ? 'border-line-strong hover:border-accent/40'
    : 'border-ink-800 hover:border-ink-600';

  return (
    <button
      onClick={() => onSelect(hotspot)}
      className={`w-full text-left rounded-[14px] border px-4 py-3.5 transition-colors shadow-sm ${bgClass} ${borderClass}`}
    >
      {/* Header row: source (left) + pick badge + score badge (right) */}
      <div className="flex items-center justify-between">
        <span
          className={`text-[12px] ${island ? 'text-content-4' : 'text-ink-500'}`}
        >
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
              精选
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

      {/* Summary (csum) */}
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

      {/* Reason callout with bold "推荐理由" label */}
      {hotspot.reason && (
        <div className="topic-reason-callout mt-2.5 rounded-lg border-l-[3px] border-emerald-500 bg-emerald-500/[0.07] px-3 py-2 text-[12px] text-[#0f766e]">
          <span className="font-bold block mb-0.5">推荐理由</span>
          {hotspot.reason}
        </div>
      )}
    </button>
  );
};
