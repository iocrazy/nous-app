// frontend/components/TopicInspiration/HotspotCard.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { islandUI } from '../../utils/featureFlags';

export const HotspotCard: React.FC<{ hotspot: Hotspot; onSelect: (h: Hotspot) => void }> = ({
  hotspot,
  onSelect,
}) => {
  const island = islandUI();
  return (
    <button
      onClick={() => onSelect(hotspot)}
      className={`w-full text-left rounded-[var(--r-lg)] border p-4 transition-colors ${
        island
          ? 'bg-island border-line-strong hover:border-accent/40'
          : 'bg-ink-900 border-ink-800 hover:border-ink-600'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={island ? 'text-content-4 text-xs' : 'text-ink-500 text-xs'}>
          {hotspot.source_label}
        </span>
        {typeof hotspot.score === 'number' && (
          <span className="text-xs font-bold text-[var(--amb-tx,#b45309)] bg-amber-500/12 rounded-full px-2 py-0.5">
            {Math.round(hotspot.score * 100)}
          </span>
        )}
      </div>
      <h4 className={`text-[15px] font-semibold mt-1.5 mb-1 ${island ? 'text-content' : 'text-ink-100'}`}>
        {hotspot.title}
      </h4>
      {hotspot.ai_summary || hotspot.summary ? (
        <p className={`text-[13px] leading-relaxed ${island ? 'text-content-2' : 'text-ink-300'}`}>
          {hotspot.ai_summary || hotspot.summary}
        </p>
      ) : null}
      {hotspot.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {hotspot.tags.map((tag) => (
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
      {hotspot.reason && (
        <div className="mt-2.5 rounded-lg border-l-[3px] border-emerald-500 bg-emerald-500/[0.07] px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">
          {hotspot.reason}
        </div>
      )}
    </button>
  );
};
