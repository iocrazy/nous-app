// frontend/components/TopicInspiration/CurrentHotspots.tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { Hotspot } from '../../services/topicService';
import { islandUI } from '../../utils/featureFlags';

function relativeTime(iso?: string | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return 'just now';
  if (h === 1) return '1h ago';
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? '1d ago' : `${d}d ago`;
}

export const CurrentHotspots: React.FC<{ items: Hotspot[] }> = ({ items }) => {
  const { t } = useTranslation();
  const island = islandUI();

  if (items.length === 0) return null;

  return (
    <div className="mb-5">
      {/* Section header */}
      <div
        className={`text-[11px] font-bold tracking-wide mb-2.5 ${
          island ? 'text-content-3' : 'text-ink-500'
        }`}
      >
        {t('topic.currentHot', 'Current Hotspots')}
      </div>

      {/* Ranked list block */}
      <div
        className={`rounded-[12px] px-3.5 py-1 ${
          island ? 'bg-island-2' : 'bg-ink-800'
        }`}
      >
        {items.map((item, i) => (
          <div
            key={item.id}
            className={`flex items-center justify-between py-1.5 ${
              i > 0 ? 'border-t border-line' : ''
            }`}
          >
            {/* Rank + title */}
            <span className="flex items-center gap-2 min-w-0 flex-1">
              <span
                className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-[6px] text-[11px] font-bold shrink-0"
                style={{
                  color: 'var(--amb-tx, #b45309)',
                  background: 'var(--amb-bg, rgba(245,158,11,.12))',
                }}
              >
                {i + 1}
              </span>
              <span
                className={`text-[13px] truncate ${
                  island ? 'text-content' : 'text-ink-100'
                }`}
              >
                {item.title}
              </span>
            </span>

            {/* Multi-source heat: how many platforms carry this topic + when.
                Falls back to the single source label when it's on just one.
                When multi-source, the count is hoverable and reveals WHICH
                platforms (the member source labels). */}
            {(() => {
              const isMulti = !!(item.source_count && item.source_count > 1);
              const names = item.source_names ?? [];
              const showTip = isMulti && names.length > 0;
              const left = isMulti
                ? t('topic.sourcesCount', {
                    count: item.source_count as number,
                    defaultValue: '{{count}} sources',
                  })
                : item.source_label;
              const time = relativeTime(item.captured_at);
              return (
                <span
                  className={`text-[11px] shrink-0 ml-3 flex items-center gap-1 ${
                    island ? 'text-content-4' : 'text-ink-500'
                  }`}
                >
                  <span className={showTip ? 'relative group/src cursor-help' : undefined}>
                    <span
                      className={
                        showTip ? 'underline decoration-dotted underline-offset-2' : undefined
                      }
                    >
                      {left}
                    </span>
                    {showTip && (
                      <span
                        className="pointer-events-none absolute right-0 top-full mt-1 z-30 hidden group-hover/src:block whitespace-nowrap rounded-lg border border-line px-2.5 py-1.5 text-left text-[11px] text-content shadow-lg"
                        style={{ background: 'var(--island, #fff)' }}
                      >
                        {names.map((n) => (
                          <span key={n} className="block leading-relaxed">
                            {n}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                  {time && <span>· {time}</span>}
                </span>
              );
            })()}
          </div>
        ))}
      </div>
    </div>
  );
};
