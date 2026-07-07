// frontend/components/Inspiration/HotspotsWorkspace.tsx
// Hotspots tab main area: ranked list (left) + inline detail (right).
// Date comes from the page-wide selected date; Not-interested hides via applyState.
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flame } from 'lucide-react';
import { useHotspots } from './useHotspots';
import { HotspotDetail } from './HotspotDetail';
import { topHotspots } from '../TopicInspiration/hotspotRanking';
import type { Hotspot } from '../../services/topicService';

interface Props {
  day: string | null;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
}

export const HotspotsWorkspace: React.FC<Props> = ({ day, onSaveAsNote, onParse }) => {
  const { t } = useTranslation();
  const { hotspots, loading, applyState } = useHotspots({ enabled: true, day: day ?? undefined });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ranked = useMemo(() => topHotspots(hotspots.filter((h) => !h.is_hidden), 50), [hotspots]);
  // Do not auto-select the top row: the detail pane would then repeat its title,
  // making it ambiguous with the list row's own title text.
  const selected = ranked.find((h) => h.id === selectedId) ?? null;

  const notInterested = async (h: Hotspot) => {
    try {
      await applyState(h, { is_hidden: true });
    } catch (err) {
      console.error('hide hotspot failed', err);
    }
  };

  if (!loading && ranked.length === 0) {
    return (
      <div className="rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.noHotspots', 'No hotspots for this day.')}
      </div>
    );
  }

  return (
    <div className="flex gap-2.5" style={{ minHeight: 420 }}>
      <div className="w-[240px] shrink-0 space-y-1.5 overflow-y-auto rounded-xl bg-island p-2">
        {ranked.map((h) => (
          <button
            key={h.id}
            onClick={() => setSelectedId(h.id)}
            className={`block w-full rounded-lg px-3 py-2 text-left ${
              selected?.id === h.id ? 'bg-indigo-500/15' : 'hover:bg-island-2'
            }`}
          >
            <div className={`text-[12.5px] leading-snug ${selected?.id === h.id ? 'text-indigo-300' : 'text-content'}`}>
              {h.title}
            </div>
            <div className="mt-1 flex items-center gap-2 text-[10px] text-content-4 tabular-nums">
              {h.source_label && <span>{h.source_label}</span>}
              {typeof h.heat === 'number' && (
                <span className="inline-flex items-center gap-0.5">
                  <Flame size={9} /> {h.heat}
                </span>
              )}
            </div>
          </button>
        ))}
      </div>
      <div className="min-w-0 flex-1">
        <HotspotDetail hotspot={selected} onSaveAsNote={onSaveAsNote} onParse={onParse} onNotInterested={notInterested} />
      </div>
    </div>
  );
};
