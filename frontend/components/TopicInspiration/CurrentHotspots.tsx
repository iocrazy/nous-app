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

            {/* Source + time meta */}
            <span
              className={`text-[11px] shrink-0 ml-3 ${
                island ? 'text-content-4' : 'text-ink-500'
              }`}
            >
              {[item.source_label, relativeTime(item.captured_at)]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
