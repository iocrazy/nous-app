// frontend/components/Inspiration/HotspotsSidePanel.tsx
// Notes-tab sidebar: top-3 hotspots for the selected day + save-as-note + open-all.
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useHotspots } from './useHotspots';
import { topHotspots } from '../TopicInspiration/hotspotRanking';
import type { Hotspot } from '../../services/topicService';

interface Props {
  day: string | null;
  onSaveAsNote: (h: Hotspot) => void;
  onOpenAll: () => void;
}

export const HotspotsSidePanel: React.FC<Props> = ({ day, onSaveAsNote, onOpenAll }) => {
  const { t } = useTranslation();
  const { hotspots } = useHotspots({ enabled: true, day: day ?? undefined });
  const top3 = useMemo(() => topHotspots(hotspots.filter((h) => !h.is_hidden), 3), [hotspots]);

  if (top3.length === 0) return null;

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        <span>◆</span>
        {t('inspiration.hotspots', 'Hotspots')}
        {day && <span className="font-normal normal-case tracking-normal text-content-4">· {day}</span>}
      </h3>
      <div className="space-y-1">
        {top3.map((h, i) => (
          <div key={h.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0 first:pt-0">
            <span className="w-3.5 pt-0.5 text-[11px] font-bold text-content-4 tabular-nums">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px] leading-snug text-content">{h.title}</div>
              <div className="mt-0.5 flex items-center gap-2 text-[10px] text-content-4 tabular-nums">
                {h.source_label && <span>{h.source_label}</span>}
                {typeof h.heat === 'number' && <span>{h.heat}</span>}
              </div>
            </div>
            <button
              aria-label="Save as note"
              onClick={() => onSaveAsNote(h)}
              className="shrink-0 rounded-md bg-island-2 p-1 text-content-3 hover:bg-indigo-500/15 hover:text-indigo-300"
            >
              +
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2.5 border-t border-line pt-2.5">
        <button onClick={onOpenAll} className="text-[11.5px] text-content-3 hover:text-content-2">
          {t('inspiration.allHotspots', 'All hotspots →')}
        </button>
      </div>
    </div>
  );
};
